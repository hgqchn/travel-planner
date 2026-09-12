const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../public/app.js'), 'utf8');
function harness({ search = '', mode = 'join', selected = 'main', result = {}, error, marker } = {}) {
  const requests = [], navigation = [], button = { disabled: false }, storage = new Map();
  if (marker) storage.set('trip-enter-selected', marker);
  const nodes = { '#project-mode': { value: mode }, '#project-list': { value: selected }, '#project-create-name': { value: '周末旅行' } };
  const env = {
    URL, URLSearchParams,
    location: { search, hash: '#itinerary', origin: 'http://localhost', assign: (url) => navigation.push(url) },
    window: {}, document: { querySelector: (id) => nodes[id] },
    sessionStorage: { getItem: (k) => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: (k) => storage.delete(k) },
    dom: { projectForm: { querySelector: () => button }, projectCodeInput: { value: '密码', disabled: false, setAttribute() {}, select() {} }, projectError: { textContent: '' } },
    entered: 0, refreshed: 0,
    async fetch(url, options) { requests.push({ url, options }); if (error) throw error; return { ok: true, json: async () => result }; },
    async requestJson() { return { data: { unlocked: true } }; },
    registerWebMcpTools() {}, showProjectGate() {}, async refreshProjectChoices() { env.refreshed++; },
  };
  const context = vm.createContext(env);
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../public/project-scope.js'), 'utf8'), context);
  vm.runInContext('async function enterUnlockedProject() { entered++; }', context);
  vm.runInContext(source.slice(source.indexOf('async function submitProject('), source.indexOf('async function lockProject(')), context);
  vm.runInContext(source.slice(source.indexOf('async function start()'), source.indexOf('window.TripProject.initLinks();')), context);
  return { env, requests, navigation, button, storage, submit: () => env.submitProject({ preventDefault() {} }), start: () => env.start() };
}
test('selected project is unlocked with its own header before redirect', async () => {
  const h = harness({ selected: '0123456789abcdef' }); await h.submit();
  assert.equal(h.requests[0].url, '/api/project-session');
  assert.equal(h.requests[0].options.headers['X-Trip-Project'], '0123456789abcdef');
  assert.deepEqual(h.navigation, ['/?project=0123456789abcdef#itinerary']);
  assert.equal(h.env.entered, 0);
});
test('passwordless creation sends empty password and marks one-time navigation', async () => {
  const h = harness({ mode: 'create', result: { project_id: '0123456789abcdef' } });
  h.env.dom.projectCodeInput.disabled = true; await h.submit();
  assert.equal(h.requests[0].url, '/api/projects');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { name: '周末旅行', project_code: '' });
  assert.equal(h.storage.get('trip-enter-selected'), '0123456789abcdef');
});
test('same project enters without redirect', async () => {
  const h = harness(); await h.submit(); assert.equal(h.env.entered, 1); assert.equal(h.navigation.length, 0);
});
test('failure preserves entry dialog and allows retry', async () => {
  const h = harness({ error: new Error('密码不正确') }); await h.submit();
  assert.match(h.env.dom.projectError.textContent, /密码不正确/); assert.equal(h.button.disabled, false); assert.equal(h.env.entered, 0);
});
test('every fresh visit shows choice even with an existing unlocked cookie', async () => {
  const h = harness(); await h.start(); assert.equal(h.env.refreshed, 1); assert.equal(h.env.entered, 0);
});
test('one-time selected redirect enters and consumes marker', async () => {
  const h = harness({ marker: 'main' }); await h.start(); assert.equal(h.env.entered, 1); assert.equal(h.storage.size, 0);
  await h.start(); assert.equal(h.env.entered, 1);
});
