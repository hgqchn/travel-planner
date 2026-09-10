const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness({ search = '', result = {}, error } = {}) {
  const requests = [], navigation = [], button = { disabled: false };
  const env = {
    URL, URLSearchParams, AbortController,
    location: { search, hash: '#itinerary', origin: 'http://localhost', assign: (url) => navigation.push(url) },
    window: { setTimeout, clearTimeout },
    dom: {
      projectForm: { querySelector: () => button },
      projectCodeInput: { value: '中文口令', setAttribute() {}, select() {} },
      projectError: { textContent: '' },
    },
    entered: 0,
    async requestJson(url, options) {
      requests.push({ url, options });
      if (error) throw error;
      return { data: result };
    },
    registerWebMcpTools() {}, showProjectGate() {}, setProjectGateChecking() {},
  };
  const context = vm.createContext(env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../public/project-scope.js'), 'utf8'), context);
  const source = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  vm.runInContext('async function enterUnlockedProject() { entered++; }', context);
  vm.runInContext(source.slice(source.indexOf('async function submitProject('), source.indexOf('async function lockProject(')), context);
  vm.runInContext(source.slice(source.indexOf('async function start()'), source.indexOf('window.TripProject.initLinks();')), context);
  return { env, requests, navigation, button, submit: () => env.submitProject({ preventDefault() {} }), start: () => env.start() };
}

test('homepage submits only a code and navigates to the returned project before loading data', async () => {
  const h = harness({ result: { unlocked: true, project_id: '0123456789abcdef' } });
  await h.submit();
  assert.equal(h.requests[0].url, '/api/project-entry');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { project_code: '中文口令' });
  assert.deepEqual(h.navigation, ['/?project=0123456789abcdef#itinerary']);
  assert.equal(h.env.entered, 0);
});

test('default project login enters in place without a redirect loop', async () => {
  const h = harness({ result: { unlocked: true, project_id: 'main' } });
  await h.submit();
  assert.equal(h.env.entered, 1);
  assert.equal(h.navigation.length, 0);
});

test('existing project links still verify the selected project', async () => {
  const h = harness({ search: '?project=0123456789abcdef', result: { unlocked: true } });
  await h.submit();
  assert.equal(h.requests[0].url, '/api/project-session');
  assert.equal(h.env.entered, 1);
});

test('wrong codes show the server error and allow another submission', async () => {
  const h = harness({ error: new Error('项目口令不正确，请确认后重试。') });
  await h.submit();
  assert.match(h.env.dom.projectError.textContent, /项目口令不正确/);
  assert.equal(h.button.disabled, false);
  assert.equal(h.navigation.length, 0);
  assert.equal(h.env.entered, 0);
});

test('missing default project leaves homepage ready for a code while broken links show an error', async () => {
  const error = Object.assign(new Error('项目不存在，请检查项目链接。'), { status: 404 });
  const home = harness({ error });
  await home.start();
  assert.equal(home.env.dom.projectError.textContent, '');
  const linked = harness({ search: '?project=0123456789abcdef', error });
  await linked.start();
  assert.equal(linked.env.dom.projectError.textContent, error.message);
});
