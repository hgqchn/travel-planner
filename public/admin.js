"use strict";
const message = document.querySelector("#admin-message");
const login = document.querySelector("#admin-login");
const content = document.querySelector("#admin-content");
let busy = false;
let projects = [];
let pendingProjectDelete = null;
const deleteDialog = document.querySelector("#project-delete-dialog");
const deleteForm = document.querySelector("#project-delete-form");
const deleteInput = document.querySelector("#project-delete-name");
const deleteConfirm = document.querySelector("#project-delete-confirm");
const deleteError = document.querySelector("#project-delete-error");

async function api(path, method = "GET", body) {
  const response = await fetch(`/api/admin/${path}`, {
    method, credentials: "same-origin", cache: "no-store",
    headers: window.TripProject.headers(body ? { "Content-Type": "application/json" } : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { content.hidden = true; login.hidden = false; }
    const error = new Error(data.error || "请求失败");
    error.status = response.status;
    throw error;
  }
  return data;
}

function node(tag, text) {
  const result = document.createElement(tag);
  if (text) result.textContent = text;
  return result;
}

function projectLink(label, projectId, path = "/") {
  const link = node("a", label);
  link.href = window.TripProject.url(path, projectId);
  link.dataset.projectNavigation = "true";
  return link;
}

function renderProjects(data) {
  projects = data.projects || [];
  const list = document.querySelector("#admin-projects");
  list.replaceChildren();
  if (!projects.length) {
    const empty = node("p", "还没有项目。可以在下方创建新的旅行项目。");
    empty.className = "admin-project-empty";
    list.append(empty);
  }
  for (const project of projects) {
    const current = project.id === window.TripProject.id;
    const row = node("article");
    row.className = `admin-project-row${current ? " is-current" : ""}`;
    const name = node("div");
    name.className = "admin-project-name";
    name.append(node("strong", project.name), node("small", current ? "当前管理的项目" : "独立旅行清单"));
    const links = node("div");
    links.className = "admin-project-links";
    links.append(projectLink("进入旅行页 ↗", project.id));
    if (!current) links.append(projectLink("管理项目", project.id, "/admin.html"));
    const remove = node("button", "删除项目");
    remove.type = "button";
    remove.className = "danger-button admin-project-delete";
    remove.disabled = busy;
    remove.setAttribute("aria-label", `删除项目“${project.name}”`);
    remove.onclick = () => openProjectDelete(project);
    links.append(remove);
    row.append(name, links);
    list.append(row);
  }
  if (!projects.some((project) => project.id === window.TripProject.id)) showProjectHub();
}

function showProjectHub() {
  login.hidden = true;
  content.hidden = false;
  document.querySelector("#admin-current-project").textContent = "旅游规划 · 全部项目";
  document.querySelector("#admin-project-details").hidden = true;
  document.querySelector("#admin-return-project").hidden = true;
}

function render(data) {
  login.hidden = true;
  content.hidden = false;
  document.querySelector("#admin-project-details").hidden = false;
  document.querySelector("#admin-return-project").hidden = false;
  document.querySelector("#admin-current-project").textContent = `当前项目 · ${data.project.name}`;
  document.querySelector("#project-settings").elements.name.value = data.project.name;
  {
    const list = document.querySelector("#admin-users");
    list.replaceChildren();
    for (const entry of data.users) {
      const row = node("div");
      row.className = "admin-row";
      const label = entry.user_id;
      const input = node("input");
      input.value = label;
      input.maxLength = 20;
      input.setAttribute("aria-label", `修改${label}`);
      const save = node("button", "改名");
      save.className = "secondary-button";
      save.disabled = busy;
      const remove = node("button", "删除");
      remove.className = "danger-button";
      remove.disabled = busy;
      save.onclick = () => perform(async () => render(await api("users", "PUT", { user_id: entry.user_id, new_user_id: input.value })));
      remove.onclick = () => {
        if (confirm(`确定从当前项目删除“${label}”吗？历史内容和署名会保留。`)) perform(async () => render(await api("users", "DELETE", { user_id: entry.user_id })));
      };
      row.append(input, save, remove);
      list.append(row);
    }
  }
}

async function loadAdmin() {
  const allProjects = await api("projects");
  renderProjects(allProjects);
  if (projects.some((project) => project.id === window.TripProject.id)) render(await api("state"));
  else showProjectHub();
}

function updateControls() {
  document.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  deleteInput.disabled = busy;
  deleteConfirm.disabled = busy || !pendingProjectDelete || pendingProjectDelete.stale || deleteInput.value !== pendingProjectDelete.name;
}

function openProjectDelete(project) {
  if (busy) return;
  pendingProjectDelete = { ...project };
  deleteInput.value = "";
  deleteError.textContent = "";
  document.querySelector("#project-delete-target").textContent = project.name;
  updateControls();
  deleteDialog.showModal();
  deleteInput.focus();
}

function closeProjectDelete() {
  if (busy) return;
  deleteDialog.close();
  pendingProjectDelete = null;
  updateControls();
}

deleteInput.addEventListener("input", updateControls);
document.querySelector("#project-delete-cancel").onclick = closeProjectDelete;
deleteDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeProjectDelete();
});
deleteForm.onsubmit = (event) => {
  event.preventDefault();
  if (busy || !pendingProjectDelete || pendingProjectDelete.stale || deleteInput.value !== pendingProjectDelete.name) return;
  const target = { ...pendingProjectDelete };
  return perform(async () => {
    deleteError.textContent = "";
    let result;
    try {
      result = await api(`projects/${encodeURIComponent(target.id)}`, "DELETE", { confirm_name: deleteInput.value });
    } catch (error) {
      if (error.status === 409) {
        pendingProjectDelete.stale = true;
        error.message = `${error.message} 请关闭此窗口，刷新项目列表后重试。`;
      }
      deleteError.textContent = error.message;
      throw error;
    }
    deleteDialog.close();
    pendingProjectDelete = null;
    const created = document.querySelector("#project-created");
    if (created.dataset.projectId === target.id) {
      created.replaceChildren();
      created.hidden = true;
      delete created.dataset.projectId;
    }
    renderProjects(result);
    if (target.id === window.TripProject.id || !projects.length) {
      const next = result.next_project_id;
      window.location.assign(next ? window.TripProject.url("/admin.html", next) : "/admin.html");
    }
    return `已删除项目“${target.name}”。其他项目和全局地点、美食缓存不受影响。`;
  });
};

async function perform(action) {
  if (busy) return;
  busy = true;
  message.textContent = "正在处理…";
  updateControls();
  try {
    const result = await action();
    message.textContent = typeof result === "string" ? result : "已完成";
  } catch (error) { message.textContent = error.message; }
  finally {
    busy = false;
    updateControls();
  }
}

login.onsubmit = (event) => {
  event.preventDefault();
  perform(async () => {
    await api("session", "POST", { password: login.elements.password.value });
    login.reset();
    await loadAdmin();
  });
};

document.querySelector("#project-settings").onsubmit = (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  perform(async () => {
    const data = await api("project", "PUT", Object.fromEntries(new FormData(form)));
    render(data);
    form.elements.project_code.value = "";
    renderProjects({ projects: projects.map((project) => project.id === window.TripProject.id ? { ...project, name: data.project.name } : project) });
  });
};

document.querySelector("#project-create").onsubmit = (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  perform(async () => {
    const { project } = await api("projects", "POST", Object.fromEntries(new FormData(form)));
    form.reset();
    renderProjects({ projects: [...projects, project] });
    const result = document.querySelector("#project-created");
    result.dataset.projectId = project.id;
    result.replaceChildren(node("p", `“${project.name}”已创建。将项目链接分享给同行者，并单独告知你刚设置的口令。首次进入需要输入该口令。`), projectLink("打开新项目 ↗", project.id), projectLink("管理新项目", project.id, "/admin.html"));
    result.hidden = false;
    return "新项目已创建，可通过下方链接进入或管理。";
  });
};

document.querySelector("#projects-refresh").onclick = () => perform(async () => renderProjects(await api("projects")));
  document.querySelector("#user-add").onsubmit = (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    perform(async () => {
      render(await api("users", "POST", Object.fromEntries(new FormData(form))));
      form.reset();
    });
  };
document.querySelector("#admin-logout").onclick = () => perform(async () => {
  await api("session", "DELETE", {});
  content.hidden = true;
  login.hidden = false;
});

window.TripProject.initLinks();
document.addEventListener("click", (event) => {
  if (busy && event.target.closest("a[data-project-path], a[data-project-navigation]")) {
    event.preventDefault();
    message.textContent = "正在处理，请稍候再切换项目。";
  }
});
perform(async () => {
  const session = await api("session");
  if (session.authenticated) await loadAdmin();
  else if (!session.enabled) throw new Error("请部署者配置 TRIP_ADMIN_PASSWORD 并重启服务。");
  else return "输入管理员密码后，可以创建或管理项目。";
});
