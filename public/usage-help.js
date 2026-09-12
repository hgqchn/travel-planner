"use strict";

(() => {
  const dialog = document.getElementById('usage-help-dialog');
  const start = dialog.querySelector('[data-help-tour]');
  const hint = dialog.querySelector('[data-help-tour-hint]');
  let opener = null, returnFocus = null, index = 0, frame = 0, active = false, context = '';
  const steps = [
    ['#city-select', '选择旅行城市', '从这里选择或添加城市。地点、美食和行程都按当前项目、当前城市保存。'],
    ['#ai-open', '让 AI 帮你安排旅程', '蓝色入口可生成完整行程，也可只收集地点和美食。生成后先核对、编辑并勾选草稿，再确认导入。'],
    ['.daily-day-add', '按天安排，也可以从空白日开始', '手动安排时先新增日期，再从地点清单添加活动。AI 已生成日期时，无需重复新增。'],
    ['.daily-day-picker', '选择要查看的日期', '每次显示一天，使用日期下拉框或上一天、下一天切换。', '当前还没有日期。结束引导后可新增一天，或先用 AI 生成并确认行程。'],
    ['[data-tour-target="daily-confirm"]', '确认当天行程', '添加地点、调整顺序，或设置出发地与返回地。可微调安排、补全停留时长和开放时间，备选地点也可恢复。', '有了日期后，这里会出现“确定当天行程”，用于选择和调整当天地点。'],
    ['.daily-slot-links', '快速跳转时段', '时段可安排多个地点，也可留空。电脑右侧为纵向导航，手机可横向滑动；点击跳转到对应时段。20:00–22:00 可直接规划。', '创建日期后将显示上午至夜间的时段，以及对应的快速跳转列表。'],
    ['[data-tour-target="daily-route"]', '规划并保存完整路线', '确认各段具体地点与交通方式，再逐段规划。路线自动保存，行程页地图显示已保存的全程路线；点击地图可重新规划。', '创建当天行程后，点击“规划路线”确认位置和交通方式，逐段保存路线。'],
    ['#export-open', '导出后随身查看', '可导出当前城市或全部城市的 Word、Excel，包含已保存的行程和离线路线轨迹图。修改行程后重新导出。', '有了行程后，可从行程页导出 Word 或 Excel。'],
  ];
  const tour = document.createElement('dialog');
  tour.className = 'usage-tour';
  tour.setAttribute('aria-labelledby', 'usage-tour-title');
  tour.setAttribute('aria-describedby', 'usage-tour-description');
  tour.innerHTML = `<div class="usage-tour-spot" aria-hidden="true"></div><section class="usage-tour-card"><p class="usage-tour-progress" aria-live="polite"></p><h2 id="usage-tour-title"></h2><p id="usage-tour-description"></p><div class="usage-tour-actions"><button type="button" class="secondary-button" data-tour-exit>退出引导</button><button type="button" class="secondary-button" data-tour-back>上一步</button><button type="button" class="primary-button" data-tour-next>下一步</button></div></section>`;
  document.body.append(tour);
  const spot = tour.querySelector('.usage-tour-spot'), card = tour.querySelector('.usage-tour-card');
  const back = tour.querySelector('[data-tour-back]'), next = tour.querySelector('[data-tour-next]');
  const scope = () => typeof state === 'undefined' ? '' : JSON.stringify([state.projectId,state.cityId,state.userId,state.projectUnlocked]);
  const ready = () => typeof state !== 'undefined' && state.projectUnlocked && !!state.userId && !document.querySelector('#project-dialog[open], #identity-dialog[open]');
  const visible = node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  function locate() {
    const target = document.querySelector(steps[index][0]);
    return visible(target) ? target : document.querySelector('.daily-day-add') || document.querySelector('#ai-open');
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
    const below = rect.bottom + 16, cardRect = card.getBoundingClientRect();
    const cardHeight = cardRect.height, cardWidth = cardRect.width;
    const beside = below + cardHeight >= height - 12 && rect.left - cardWidth - 16 >= 12;
    const cardTop = beside ? Math.min(rect.top,height-cardHeight-12) : below + cardHeight < height - 12 ? below : rect.top - cardHeight - 16 >= 12 ? rect.top - cardHeight - 16 : height - cardHeight - 12;
    card.style.top = Math.max(12,cardTop)+'px';
    card.style.left = beside ? (rect.left-cardWidth-16)+'px' : Math.max(12,Math.min(width-cardWidth-12,rect.left))+'px';
  }
  function schedule() { if (active && !frame) frame = requestAnimationFrame(position); }
  function show() {
    const step = steps[index], target = document.querySelector(step[0]);
    tour.querySelector('.usage-tour-progress').textContent = `流程引导 · ${index+1} / ${steps.length}`;
    tour.querySelector('h2').textContent = step[1];
    tour.querySelector('#usage-tour-description').textContent = visible(target) ? step[2] : step[3] || step[2];
    back.disabled = index === 0; next.textContent = index === steps.length-1 ? '完成引导' : '下一步';
    locate()?.scrollIntoView({block:'center',behavior:'instant'});
    position(); next.focus({preventScroll:true});
  }
  function finish() {
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
    hint.textContent = ready() ? '逐步聚焦主要入口，只介绍操作，不会生成或修改行程。可随时退出。' : '请先进入项目并选择用户 ID，再从主页“使用帮助”开始流程引导。';
    dialog.showModal(); dialog.querySelector('.usage-help-content').scrollTop = 0;
  }));
  dialog.querySelectorAll('[data-help-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => { if (!active && opener?.isConnected) opener.focus({preventScroll:true}); opener = null; });
  start.addEventListener('click', () => {
    if (!ready()) return;
    returnFocus = opener; active = true; dialog.close();
    document.querySelector('[data-tab="itinerary"]')?.click();
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
