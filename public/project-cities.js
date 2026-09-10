"use strict";

window.TripCities = (() => {
  const $ = (id) => document.getElementById(id);
  let signature = "";
  let acknowledged = new Set();
  let conflicts = [];
  const key = (conflict) => JSON.stringify(conflict);

  function showConflicts(manual = false) {
    if (!state.projectUnlocked || !state.userId || !conflicts.length) return;
    const dialog = $("city-conflict-dialog");
    if (!manual && !dialog.open && conflicts.every((conflict) => acknowledged.has(key(conflict)))) return;
    // Let the user finish an active edit or identity selection before reminding.
    if (document.querySelector("#edit-dialog[open], #city-plan-dialog[open], #identity-dialog[open], #project-dialog[open]")) return;
    $("city-conflict-list").replaceChildren(...conflicts.map((conflict) => {
      const section = element("section", "city-conflict-day");
      section.append(element("h3", "", conflict.date));
      const list = element("ul");
      for (const item of conflict.items) {
        list.append(element("li", "", `${item.start_time || "时间待定"} · ${item.city_name} · ${item.title}`));
      }
      section.append(list);
      return section;
    }));
    if (!dialog.open) dialog.showModal();
    acknowledged = new Set(conflicts.map(key));
  }

  function render() {
    window.TripCityEditor?.sync();
    const visible = state.projectUnlocked && Boolean(state.userId);
    $("project-cities").hidden = !visible;
    if (!visible) {
      signature = "";
      acknowledged.clear();
      conflicts = [];
      $("project-city-list").replaceChildren();
      $("city-conflict-list").replaceChildren();
      if ($("city-conflict-dialog").open) $("city-conflict-dialog").close();
      return;
    }
    const plan = state.projectItinerary;
    conflicts = plan?.conflicts || [];
    // Resolved conflicts can be reported again if they are later reintroduced.
    const current = new Set(conflicts.map(key));
    acknowledged = new Set([...acknowledged].filter((value) => current.has(value)));
    const next = JSON.stringify([state.cityId, plan]);
    if (signature !== next) {
      signature = next;
      const cities = plan?.cities || [];
      $("project-cities-count").textContent = `已规划 ${cities.length} 个城市`;
      $("project-cities-empty").hidden = Boolean(cities.length);
      $("project-cities-empty").textContent = plan ? "为城市添加或导入行程后，会在这里显示。" : "正在读取项目城市…";
      const focusedCity = document.activeElement?.dataset?.projectCity;
      $("project-city-list").replaceChildren(...cities.map((city) => {
        const active = city.city_id === state.cityId;
        const card = element("div", `project-city-card${active ? " is-current" : ""}`);
        const button = element("button", "project-city");
        button.type = "button";
        button.dataset.projectCity = city.city_id;
        button.setAttribute("aria-pressed", String(active));
        button.append(element("strong", "", `${city.city_name}${active ? " · 当前" : ""}`));
        button.append(element("span", "", `${city.count} 项行程 · ${city.dates.length} 天`));
        button.append(element("small", "", city.dates.join("、")));
        button.addEventListener("click", () => window.TripUI.switchCity(city.city_id));
        const edit = element("button", "project-city-edit", "编辑城市");
        edit.type = "button";
        edit.setAttribute("aria-label", `编辑${city.city_name}的出行日期或移除城市`);
        edit.setAttribute("aria-haspopup", "dialog");
        edit.disabled = !city.version;
        edit.addEventListener("click", () => window.TripCityEditor.open(city));
        card.append(button, edit);
        return card;
      }));
      if (focusedCity) [...$("project-city-list").children].map(node => node.children[0]).find(node => node.dataset.projectCity === focusedCity)?.focus({ preventScroll: true });
      $("city-conflict-open").hidden = !conflicts.length;
      $("city-conflict-open").textContent = `${conflicts.length} 天安排了多个城市 · 查看时间提醒`;
    }
    if (!conflicts.length && $("city-conflict-dialog").open) $("city-conflict-dialog").close();
    showConflicts();
  }

  function init() {
    $("city-conflict-open").addEventListener("click", () => showConflicts(true));
    for (const id of ["city-conflict-close", "city-conflict-keep"]) {
      $(id).addEventListener("click", () => $("city-conflict-dialog").close());
    }
    document.addEventListener("close", () => showConflicts(), true);
  }
  return { render, init };
})();
