"use strict";

window.TripProject = (() => {
  const params = new URLSearchParams(location.search);
  const id = params.has("project") ? params.get("project") : "main";
  const entryByCode = !params.has("project");
  const validId = (value) => value === "main" || /^[0-9a-f]{16}$/.test(value);

  function requireValid() {
    if (validId(id)) return;
    const error = new Error("项目链接无效，请检查分享链接中的项目地址。");
    error.status = 404;
    error.data = { code: "INVALID_PROJECT" };
    throw error;
  }

  function headers(extra = {}) {
    requireValid();
    return { ...extra, "X-Trip-Project": id };
  }

  function url(path = "/", projectId = id) {
    const result = new URL(path, document.baseURI || location.origin);
    if (projectId === "main") result.searchParams.delete("project");
    else result.searchParams.set("project", projectId);
    return `${result.pathname}${result.search}${result.hash}`;
  }

  function storageKey(base) { return id === "main" ? base : `${base}:${id}`; }

  function initLinks() {
    document.querySelectorAll("[data-project-path]").forEach((link) => {
      link.href = url(link.dataset.projectPath);
    });
    document.addEventListener("click", (event) => {
      if (event.target.closest("a[data-project-path], a[data-project-navigation]") && window.TripBatch && !window.TripBatch.canNavigate()) event.preventDefault();
    });
  }

  return { id, entryByCode, valid: validId(id), requireValid, headers, url, storageKey, initLinks };
})();
