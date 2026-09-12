"use strict";

(() => {
  if (document.documentElement.dataset.usageDemo === 'true') return;
  const dialog = document.getElementById('usage-help-dialog');
  const steps = window.TripUsageTour.steps;
  const start = dialog.querySelector('[data-help-tour]'), hint = dialog.querySelector('[data-help-tour-hint]');
  let opener, returnFocus, index = 0, active = false, busy = false, context = '', generation = 0, frame = 0, template;
  const tour = document.createElement('dialog');
  tour.className = 'usage-tour';
  tour.setAttribute('aria-labelledby', 'usage-tour-title');
  tour.setAttribute('aria-describedby', 'usage-tour-description');
  tour.innerHTML = `<p class="usage-tour-banner">流程引导 · 预设示例 · 不调用 AI 或地图服务，不保存到项目</p><div class="usage-tour-stage"><iframe class="usage-tour-preview" title="与实际页面一致的流程示例" sandbox="allow-scripts allow-same-origin allow-forms" tabindex="-1" inert></iframe><div class="usage-tour-spot" aria-hidden="true"></div></div><section class="usage-tour-card"><div class="usage-tour-copy"><p class="usage-tour-progress" aria-live="polite"></p><h2 id="usage-tour-title"></h2><p id="usage-tour-description"></p></div><div class="usage-tour-actions"><button type="button" class="secondary-button" data-tour-exit>退出引导</button><button type="button" class="secondary-button" data-tour-back>上一步</button><button type="button" class="primary-button" data-tour-next>下一步</button></div></section>`;
  document.body.append(tour);
  const preview = tour.querySelector('iframe'), spot = tour.querySelector('.usage-tour-spot');
  const back = tour.querySelector('[data-tour-back]'), next = tour.querySelector('[data-tour-next]');
  const description = tour.querySelector('#usage-tour-description');
  const scope = () => typeof state === 'undefined' ? '' : JSON.stringify([state.projectId, state.cityId, state.userId, state.projectUnlocked]);
  const ready = () => typeof state !== 'undefined' && state.projectUnlocked && !!state.userId && !document.querySelector('#project-dialog[open], #identity-dialog[open]');
  const visible = node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  const locate = () => preview.contentDocument?.querySelector(steps[index][0]);
  function position() {
    frame = 0;
    if (!active || busy) return;
    if (!ready() || scope() !== context) { tour.close(); return; }
    const target = locate();
    if (!visible(target)) { spot.hidden = true; return; }
    const rect = target.getBoundingClientRect();
    const left = Math.max(4, rect.left - 5), top = Math.max(4, rect.top - 5);
    const right = Math.min(preview.clientWidth - 4, rect.right + 5), bottom = Math.min(preview.clientHeight - 4, rect.bottom + 5);
    spot.hidden = right <= left || bottom <= top;
    Object.assign(spot.style, { left: `${left}px`, top: `${top}px`, width: `${Math.max(0, right-left)}px`, height: `${Math.max(0, bottom-top)}px` });
  }
  function schedule() { if (active && !frame) frame = requestAnimationFrame(position); }
  function show() {
    if (!locate()) throw new Error('当前步骤的页面尚未准备好，请退出后重试。');
    tour.querySelector('.usage-tour-progress').textContent = `流程引导 · ${index + 1} / ${steps.length}`;
    tour.querySelector('#usage-tour-title').textContent = steps[index][1]; description.textContent = steps[index][2];
    back.disabled = index === 0; next.disabled = false;
    next.textContent = index === steps.length - 1 ? '完成引导' : `下一步：${steps[index][3]}`;
    locate()?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
    position(); schedule(); next.focus({ preventScroll: true });
  }
  async function loadDemo(token) {
    if (!template) {
      const response = await fetch('/index.html');
      if (!response.ok) throw new Error('无法加载示例页面，请检查连接后重试。');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      doc.documentElement.dataset.usageDemo = 'true';
      doc.querySelector('script[src="/usage-help.js"]')?.remove();
      const base = doc.createElement('base'); base.href = location.origin + '/';
      const policy = doc.createElement('meta'); policy.httpEquiv = 'Content-Security-Policy';
      policy.content = `default-src 'none'; script-src ${location.origin}; style-src ${location.origin} 'unsafe-inline'; img-src ${location.origin} data:; font-src ${location.origin}; connect-src 'none'; form-action 'none'; base-uri ${location.origin}`;
      doc.head.prepend(base); doc.head.prepend(policy);
      template = '<!doctype html>' + doc.documentElement.outerHTML;
    }
    if (token !== generation) return;
    const previousDocument = preview.contentDocument;
    preview.srcdoc = template;
    const end = Date.now() + 12000;
    while (token === generation && (preview.contentDocument === previousDocument || !preview.contentWindow?.TripUsageTour?.ready)) {
      if (Date.now() > end) throw new Error('示例加载超时，请退出后重新开始。');
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    if (token !== generation) return;
    preview.contentWindow.addEventListener('scroll', schedule, true);
  }
  async function change(destination, reset = false) {
    if (busy) return;
    const token = ++generation;
    busy = true; back.disabled = next.disabled = true; spot.hidden = true;
    description.textContent = reset ? '正在载入对应步骤…' : `正在演示：${steps[index][3]}…`;
    try {
      if (reset) {
        // Rebuild and replay the same preset actions on back; no future state leaks into earlier steps.
        await loadDemo(token);
        for (let i = 0; i < destination && token === generation; i++) await preview.contentWindow.TripUsageTour.advance(i);
      } else await preview.contentWindow.TripUsageTour.advance(index);
      if (token !== generation) return;
      index = destination; busy = false; show();
    } catch (error) {
      if (token !== generation) return;
      busy = false; description.textContent = `引导演示未完成：${error.message}`;
      next.disabled = true; back.disabled = index === 0;
    }
  }
  const observer = new MutationObserver(schedule);
  function finish() {
    active = busy = false; generation++; cancelAnimationFrame(frame); frame = 0;
    preview.removeAttribute('srcdoc'); preview.src = 'about:blank';
    window.removeEventListener('resize', schedule); observer.disconnect();
    if (returnFocus?.isConnected && visible(returnFocus)) returnFocus.focus({ preventScroll: true });
    returnFocus = null;
  }
  document.querySelectorAll('[data-help-open]').forEach(button => button.addEventListener('click', () => {
    if (dialog.open) return;
    opener = button; start.disabled = !ready();
    hint.textContent = ready() ? '使用真实页面布局，逐步演示操作与结果。输入、AI 草稿和路线均为预设示例，可随时退出。' : '请先进入项目并选择用户 ID，再从主页“使用帮助”开始流程引导。';
    dialog.showModal(); dialog.querySelector('.usage-help-content').scrollTop = 0;
  }));
  dialog.querySelectorAll('[data-help-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => { if (!active && opener?.isConnected) opener.focus({ preventScroll: true }); opener = null; });
  start.addEventListener('click', () => {
    if (!ready()) return;
    returnFocus = opener; active = true; dialog.close(); index = 0; context = scope(); tour.showModal();
    window.addEventListener('resize', schedule); observer.observe(document.querySelector('main'), { childList: true, subtree: true });
    change(0, true);
  });
  back.addEventListener('click', () => { if (index > 0) change(index - 1, true); });
  next.addEventListener('click', () => { if (index === steps.length - 1) tour.close(); else change(index + 1); });
  tour.querySelector('[data-tour-exit]').addEventListener('click', () => tour.close());
  tour.addEventListener('close', finish);
})();
