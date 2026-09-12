"use strict";

(() => {
  const dialog = document.getElementById('usage-help-dialog');
  const start = dialog.querySelector('[data-help-tour]');
  const hint = dialog.querySelector('[data-help-tour-hint]');
  let opener = null, returnFocus = null, index = 0, frame = 0, active = false, context = '';
  const steps = [
    ['[data-demo="city"]', '选择旅行城市', '从这里选择或添加城市。地点、美食和行程都按当前项目、当前城市保存。'],
    ['[data-demo="ai"]', '让 AI 帮你安排旅程', '蓝色入口可生成完整行程，也可只收集地点和美食。生成后先核对、编辑并勾选草稿，再确认导入。'],
    ['[data-demo="add"]', '按天安排，也可以从空白日开始', '手动安排时先新增日期，再从地点清单添加活动。AI 已生成日期时，无需重复新增。'],
    ['[data-demo="day"]', '选择要查看的日期', '每次显示一天，使用日期下拉框或上一天、下一天切换。'],
    ['[data-demo="confirm"]', '确认当天行程', '添加地点、调整顺序，或设置出发地与返回地。可微调安排、补全停留时长和开放时间，备选地点也可恢复。'],
    ['[data-demo="slots"]', '快速跳转时段', '时段可安排多个地点，也可留空。电脑右侧为纵向导航，手机可横向滑动；点击跳转到对应时段。20:00–22:00 可直接规划。'],
    ['[data-demo="route"]', '规划并保存完整路线', '确认各段具体地点与交通方式，再逐段规划。路线自动保存，行程页地图显示已保存的全程路线；点击地图可重新规划。'],
    ['[data-demo="export"]', '导出后随身查看', '可导出当前城市或全部城市的 Word、Excel，包含已保存的行程和离线路线轨迹图。修改行程后重新导出。'],
  ];
  const tour = document.createElement('dialog');
  tour.className = 'usage-tour';
  tour.setAttribute('aria-labelledby', 'usage-tour-title');
  tour.setAttribute('aria-describedby', 'usage-tour-description');
  tour.innerHTML = `<div class="usage-tour-preview" role="region" aria-label="引导示例页面" tabindex="0"></div><div class="usage-tour-spot" aria-hidden="true"></div><section class="usage-tour-card"><p class="usage-tour-progress" aria-live="polite"></p><h2 id="usage-tour-title"></h2><p id="usage-tour-description"></p><div class="usage-tour-actions"><button type="button" class="secondary-button" data-tour-exit>退出引导</button><button type="button" class="secondary-button" data-tour-back>上一步</button><button type="button" class="primary-button" data-tour-next>下一步</button></div></section>`;
  document.body.append(tour);
  const spot = tour.querySelector('.usage-tour-spot'), card = tour.querySelector('.usage-tour-card');
  const back = tour.querySelector('[data-tour-back]'), next = tour.querySelector('[data-tour-next]');
  const preview = tour.querySelector('.usage-tour-preview');
  function renderDemo() {
    const imported = index >= 3, confirmed = index >= 5, routed = index >= 7;
    preview.innerHTML = `<div class="usage-demo-page">
      <p class="usage-demo-notice">引导示例 · 点击“下一步”模拟操作，不会保存到项目</p>
      <header class="usage-demo-toolbar"><strong>即刻出发 · 示例旅行</strong><span class="usage-demo-control" data-demo="city">⌖ ${index ? '上海' : '选择城市'} ⌄</span></header>
      <section class="usage-demo-cover"><small>和同行的人，去向往的地方</small><h2>下一站，上海。</h2><p>${imported ? '2 天行程 · 城市漫步' : '从一份期待，开始安排旅程。'}</p></section>
      <div class="usage-demo-ai" data-demo="ai"><strong>AI 帮你安排旅程</strong><span>生成完整行程，或收集地点与美食 →</span></div>
      ${index === 2 ? '<section class="usage-demo-panel"><strong>AI 草稿 · 已生成，待确认导入</strong><p>✓ 第 1 天：豫园 → 外滩</p><p>✓ 第 2 天：上海博物馆 → 武康路</p><span class="usage-demo-control">确认导入所选行程</span></section>' : ''}
      <section class="usage-demo-panel"><div class="usage-demo-toolbar"><h3>每日行程</h3><span class="usage-demo-control" data-demo="add">＋ 新增日期</span></div>
      ${imported ? `<div class="usage-demo-toolbar"><span class="usage-demo-control" data-demo="day">‹　第 1 天 · 10 月 1 日　⌄　›</span><span>共 2 天</span></div>
      <div class="usage-demo-toolbar usage-demo-tools"><span class="usage-demo-control" data-demo="confirm">确定当天行程</span><span class="usage-demo-control" data-demo="route">规划路线</span><span class="usage-demo-control" data-demo="export">导出 Word / Excel</span></div>
      <p class="usage-demo-status">${routed ? '✓ 示例路线已保存 · 可导出随身查看' : confirmed ? '✓ 已确认当天行程 · 已补全停留时长' : '✓ 已导入 AI 草稿 · 请核对当天安排'}</p>
      <div class="usage-demo-control usage-demo-slots" data-demo="slots">上午　·　午餐　·　下午　·　晚餐　·　夜间</div>
      <article class="usage-demo-stop"><small>上午 · 09:00–11:00</small><h3>豫园</h3><p>${confirmed ? '停留 2 小时 · 已确认顺序与地点' : '建议停留 2 小时 · 待确认'}</p></article>
      <article class="usage-demo-stop"><small>下午 · 14:00–16:00</small><h3>外滩</h3><p>沿江漫步，欣赏城市风景</p></article>
      ${routed ? '<div class="usage-demo-map" aria-label="示例路线示意图"><strong>已保存的全程路线</strong><p>① 出发地 ── ② 豫园 ── ③ 外滩 ── ④ 返回地</p><small>示意路线 · 公交地铁 / 步行</small></div>' : ''}` : '<p class="usage-demo-empty">还没有日期，可以导入 AI 草稿，也可以新增空白日。</p>'}
      </section></div>`;
  }
  const scope = () => typeof state === 'undefined' ? '' : JSON.stringify([state.projectId,state.cityId,state.userId,state.projectUnlocked]);
  const ready = () => typeof state !== 'undefined' && state.projectUnlocked && !!state.userId && !document.querySelector('#project-dialog[open], #identity-dialog[open]');
  const visible = node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  function locate() {
    return preview.querySelector(steps[index][0]);
  }
  function position() {
    frame = 0;
    if (!active) return;
    if (!ready() || scope() !== context) { tour.close(); return; }
    const target = locate();
    if (!visible(target)) { tour.close(); return; }
    const rect = target.getBoundingClientRect();
    const width = window.innerWidth, height = window.innerHeight;
    const left = Math.max(4, rect.left - 5), top = Math.max(4, rect.top - 5);
    Object.assign(spot.style, {left:left+'px',top:top+'px',width:Math.max(0,Math.min(width-4,rect.right+5)-left)+'px',height:Math.max(0,Math.min(height-4,rect.bottom+5)-top)+'px'});

  }
  function schedule() { if (active && !frame) frame = requestAnimationFrame(position); }
  function show() {
    renderDemo();
    const step = steps[index];
    tour.querySelector('.usage-tour-progress').textContent = `流程引导 · ${index+1} / ${steps.length}`;
    tour.querySelector('#usage-tour-title').textContent = step[1];
    tour.querySelector('#usage-tour-description').textContent = step[2];
    back.disabled = index === 0; next.textContent = index === steps.length-1 ? '完成引导' : '下一步';
    locate()?.scrollIntoView({block:'center',behavior:'instant'});
    position(); next.focus({preventScroll:true});
  }
  function finish() {
    preview.innerHTML = '';
    active = false; cancelAnimationFrame(frame); frame = 0;
    window.removeEventListener('resize', schedule); window.removeEventListener('scroll', schedule, true);
    observer.disconnect();
    if (returnFocus?.isConnected && visible(returnFocus)) returnFocus.focus({preventScroll:true});
    returnFocus = null;
  }
  const observer = new MutationObserver(schedule);
  document.querySelectorAll('[data-help-open]').forEach(button => button.addEventListener('click', () => {
    if (dialog.open) return;
    opener = button; start.disabled = !ready();
    hint.textContent = ready() ? '通过示例页面逐步模拟规划操作，无需已有行程，不会修改实际数据。可随时退出。' : '请先进入项目并选择用户 ID，再从主页“使用帮助”开始流程引导。';
    dialog.showModal(); dialog.querySelector('.usage-help-content').scrollTop = 0;
  }));
  dialog.querySelectorAll('[data-help-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => { if (!active && opener?.isConnected) opener.focus({preventScroll:true}); opener = null; });
  start.addEventListener('click', () => {
    if (!ready()) return;
    returnFocus = opener; active = true; dialog.close();
    index = 0; context = scope(); tour.showModal();
    window.addEventListener('resize', schedule); window.addEventListener('scroll', schedule, true);
    observer.observe(document.querySelector('main'), {childList:true,subtree:true});
    show();
  });
  back.addEventListener('click', () => { if (index > 0) { index--; show(); } });
  next.addEventListener('click', () => { if (index === steps.length-1) tour.close(); else { index++; show(); } });
  tour.querySelector('[data-tour-exit]').addEventListener('click', () => tour.close());
  tour.addEventListener('close', finish);
})();
