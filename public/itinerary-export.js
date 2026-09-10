"use strict";

window.TripExport = (() => {
  const $ = (id) => document.getElementById(id);
  let controller = null;
  let openedCityId = "";

  function busy(value) {
    ["export-word", "export-excel", "export-scope"].forEach((id) => { $(id).disabled = value; });
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
    if ($("export-dialog").open && (!state.projectUnlocked || state.cityId !== openedCityId)) {
      cancel();
      $("export-dialog").close();
    }
  }

  function open() {
    if (!state.projectUnlocked) { showProjectGate(); return; }
    openedCityId = state.cityId;
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
      const blob = await response.blob();
      if (controller !== current || current.signal.aborted || !state.projectUnlocked) return;
      let name = `行程安排.${format}`;
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
    $("export-excel").addEventListener("click", () => download("xlsx"));
  }

  return { init, sync };
})();
