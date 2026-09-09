"use strict";
const message = document.querySelector("#admin-message");
const login = document.querySelector("#admin-login");
const content = document.querySelector("#admin-content");
let busy = false;
async function api(path, method = "GET", body) {
  const response = await fetch(`/api/admin/${path}`, {method, credentials:"same-origin", cache:"no-store", headers:body ? {"Content-Type":"application/json"} : {}, body:body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { content.hidden = true; login.hidden = false; }
    throw new Error(data.error || "请求失败");
  }
  return data;
}
function node(tag, text) { const n = document.createElement(tag); if (text) n.textContent = text; return n; }
function render(data) {
  login.hidden = true; content.hidden = false;
  document.querySelector("#project-settings").elements.name.value = data.project.name;
  for (const [kind, records] of [["cities", data.cities], ["users", data.users]]) {
    const list = document.querySelector(`#admin-${kind}`); list.replaceChildren();
    for (const entry of records) {
      const row = node("div"); row.className = "admin-row";
      const label = kind === "cities" ? entry.name : entry.user_id;
      const input = node("input"); input.value = label; input.maxLength = kind === "cities" ? 30 : 20; input.setAttribute("aria-label", `修改${label}`);
      const save = node("button", "改名"); save.className = "secondary-button";
      const remove = node("button", "删除"); remove.className = "danger-button";
      save.onclick = () => perform(async () => { const result = await api(kind, "PUT", kind === "cities" ? {id:entry.id, name:input.value} : {user_id:entry.user_id, new_user_id:input.value}); render(result); });
      remove.onclick = () => { if (confirm(`确定删除“${label}”吗？${kind === "users" ? "历史内容和署名会保留。" : "只有空城市可以删除。"}`)) perform(async () => render(await api(kind, "DELETE", kind === "cities" ? {id:entry.id} : {user_id:entry.user_id}))); };
      row.append(input, save, remove); list.append(row);
    }
  }
}
async function perform(action) {
  if (busy) return; busy = true; message.textContent = "正在处理…";
  document.querySelectorAll("button").forEach(b => b.disabled = true);
  try { await action(); message.textContent = "已完成"; } catch(error) { message.textContent = error.message; }
  finally { busy = false; document.querySelectorAll("button").forEach(b => b.disabled = false); }
}
login.onsubmit = event => { event.preventDefault(); perform(async () => { await api("session", "POST", {password:login.elements.password.value}); login.reset(); render(await api("state")); }); };
document.querySelector("#project-settings").onsubmit = event => { event.preventDefault(); const form = event.currentTarget; perform(async () => { render(await api("project", "PUT", Object.fromEntries(new FormData(form)))); form.elements.project_code.value = ""; }); };
for (const [selector, kind] of [["#city-add", "cities"], ["#user-add", "users"]]) document.querySelector(selector).onsubmit = event => { event.preventDefault(); const form = event.currentTarget; perform(async () => { render(await api(kind, "POST", Object.fromEntries(new FormData(form)))); form.reset(); }); };
document.querySelector("#admin-logout").onclick = () => perform(async () => { await api("session", "DELETE", {}); content.hidden = true; login.hidden = false; });
perform(async () => { const session = await api("session"); if(session.authenticated) render(await api("state")); else if (!session.enabled) throw new Error("请部署者配置 TRIP_ADMIN_PASSWORD 并重启服务。"); });
