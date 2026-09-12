"use strict";

window.TripExport = (() => {
  const $ = (id) => document.getElementById(id);
  let controller = null;
  let openedCityId = "";
  let openedProjectId = "";

  async function imageBlob(data, signal) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('当前浏览器无法生成图片，请更换浏览器重试。');
    const font = '"PingFang SC", "Microsoft YaHei", sans-serif';
    if (document.fonts?.ready) await document.fonts.ready;
    const rows = [];
    let y = 44;
    function text(value, size = 22, color = '#243746', gap = 12) {
      ctx.font = `${size}px ${font}`;
      for (const paragraph of String(value).split('\n')) {
        let line = '';
        for (const char of paragraph) {
          if (line && ctx.measureText(line + char).width > 976) {
            rows.push({ value: line, size, color, y }); y += size * 1.5; line = '';
          }
          line += char;
        }
        rows.push({ value: line, size, color, y }); y += size * 1.5;
      }
      y += gap;
    }
    text(data.title, 36, '#123b3a');
    text(`${data.scope} · 整体行程`, 24, '#55706e', 22);
    const mapY = y; y += 628;
    text('地图：高德地图 · 彩色轨迹为已保存路段', 17, '#667876', 24);
    for (const day of data.days) {
      text(`${day.date} · ${day.city}`, 28, day.color);
      text(day.stops.join(' → ') || '当天暂无已安排地点', 20, '#55706e');
      for (const segment of day.segments) text(segment);
      if (day.backups.length) text(`备选：${day.backups.join('、')}`, 19, '#667876');
      y += 18;
    }
    // Keep one complete image; never silently clip long itineraries.
    if (y > 15000) throw new Error('行程过长，无法生成一张完整图片，请选择当前城市后分别导出。');
    canvas.width = 1080; canvas.height = Math.ceil(y + 24);
    ctx.fillStyle = '#f5f8f6'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.textBaseline = 'top';
    for (const row of rows) {
      ctx.font = `${row.size}px ${font}`; ctx.fillStyle = row.color;
      ctx.fillText(row.value, 44, row.y);
    }
    const bitmap = await new Promise((resolve, reject) => {
      const img = new Image();
      const abort = () => { img.src = ''; reject(new DOMException('Aborted', 'AbortError')); };
      const cleanup = () => signal.removeEventListener('abort', abort);
      img.onload = () => { cleanup(); resolve(img); };
      img.onerror = () => { cleanup(); reject(new Error('地图图片加载失败，请重试。')); };
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) { cleanup(); abort(); return; }
      img.src = data.map.image;
    });
    ctx.drawImage(bitmap, 40, mapY, 1000, 600);
    const project = ([lng, lat]) => {
      const x = (lng + 180) / 360;
      const yy = (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2;
      const scale = 256 * 2 ** data.map.zoom;
      return [540 + (x - data.map.center[0]) * scale, mapY + 300 + (yy - data.map.center[1]) * scale];
    };
    ctx.save(); ctx.beginPath(); ctx.rect(40, mapY, 1000, 600); ctx.clip();
    ctx.lineWidth = 5; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    for (const path of data.map.paths) {
      ctx.strokeStyle = path.color; ctx.beginPath();
      path.points.forEach((p, i) => { const [x, yy] = project(p); i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy); });
      ctx.stroke();
    }
    ctx.font = `bold 16px ${font}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    // Repeated hotel/entry coordinates share one marker listing all visit numbers.
    const markers = new Map();
    for (const pin of data.map.pins) {
      const key = pin.point.join(',');
      if (!markers.has(key)) markers.set(key, { ...pin, labels: [] });
      markers.get(key).labels.push(pin.number);
    }
    for (const pin of markers.values()) {
      const [x, yy] = project(pin.point), label = pin.labels.join('/');
      const width = Math.max(32, ctx.measureText(label).width + 16);
      ctx.fillStyle = pin.color; ctx.fillRect(x - width / 2, yy - 16, width, 32);
      ctx.fillStyle = '#ffffff'; ctx.fillText(label, x, yy);
    }
    ctx.restore();
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('图片生成失败，请缩小导出范围后重试。')), 'image/png'));
  }

  function busy(value) {
    ["export-image", "export-word", "export-excel", "export-scope"].forEach((id) => { $(id).disabled = value; });
    $("export-dialog").setAttribute("aria-busy", String(value));
  }

  function cancel() {
    controller?.abort();
    controller = null;
    busy(false);
    $("export-status").textContent = "";
  }

  function sync() {
    $("export-open").hidden = state.tab !== "itinerary" || !state.projectUnlocked;
    if ($("export-dialog").open && (!state.projectUnlocked || state.cityId !== openedCityId || state.projectId !== openedProjectId)) {
      cancel();
      $("export-dialog").close();
    }
  }

  function open() {
    if (!state.projectUnlocked) { showProjectGate(); return; }
    openedCityId = state.cityId;
    openedProjectId = state.projectId;
    const city = state.cities.find((entry) => entry.id === openedCityId);
    $("export-city-option").textContent = city ? `当前城市 · ${city.name}` : "请先选择城市";
    $("export-city-option").disabled = !city;
    $("export-scope").value = city ? "city" : "all";
    $("export-status").textContent = "";
    $("export-error").textContent = "";
    $("export-dialog").showModal();
  }

  async function download(format) {
    if (controller) return;
    const current = new AbortController();
    controller = current;
    busy(true);
    $("export-error").textContent = "";
    $("export-status").textContent = "正在整理行程并生成文件…";
    const timeout = setTimeout(() => current.abort(), 120000);
    try {
      const params = new URLSearchParams({ format, scope: $("export-scope").value });
      if (params.get("scope") === "city") params.set("city_id", openedCityId);
      const response = await fetch(`/api/itinerary/export?${params}`, {
        credentials: "same-origin", cache: "no-store",
        headers: window.TripProject.headers(), signal: current.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        if (data.code === "PROJECT_LOCKED") {
          $("export-dialog").close();
          showProjectGate();
          return;
        }
        throw new Error(data.error || "导出失败，请稍后重试。");
      }
      if (controller !== current || current.signal.aborted) return;
      const data = format === 'image' ? await response.json() : null;
      const blob = data ? await imageBlob(data, current.signal) : await response.blob();
      if (controller !== current || current.signal.aborted || !state.projectUnlocked || state.projectId !== openedProjectId) return;
      let name = data?.filename || `行程安排.${format}`;
      const encodedName = response.headers.get("Content-Disposition")?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      if (encodedName) {
        try { name = decodeURIComponent(encodedName); } catch { /* Use the fallback filename. */ }
      }
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      document.body.append(anchor);
      try { anchor.click(); } finally {
        anchor.remove();
        // Give mobile browsers time to consume the object URL.
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      }
      $("export-status").textContent = "已发起下载，可在浏览器下载记录或设备的“文件”中查看。";
    } catch (error) {
      if (controller !== current) return;
      $("export-status").textContent = "";
      $("export-error").textContent = error.name === "AbortError" ? "生成文件超时，请稍后重试。" : error.message;
    } finally {
      clearTimeout(timeout);
      if (controller === current) { controller = null; busy(false); }
    }
  }

  function init() {
    $("export-open").addEventListener("click", open);
    $("export-close").addEventListener("click", () => { cancel(); $("export-dialog").close(); });
    $("export-dialog").addEventListener("cancel", cancel);
    $("export-dialog").addEventListener("close", cancel);
    $("export-word").addEventListener("click", () => download("docx"));
    $("export-image").addEventListener("click", () => download("image"));
    $("export-excel").addEventListener("click", () => download("xlsx"));
  }

  return { init, sync };
})();
