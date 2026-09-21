"use strict";
const $ = (id) => document.getElementById(id);
let token = "";
let defaults = null;
let cursor = 0;
const names = {
  enabled: "插件总开关", group_enabled: "群聊", private_enabled: "私聊",
  pre_check_enabled: "接话判断", post_check_enabled: "发送复核",
  recall_enabled: "撤回拦截", history_enabled: "历史记录",
  context_enabled: "聊天上下文", bypass_commands: "命令旁路",
  fail_open: "故障放行", webui_enabled: "WebUI",
};
const reasons = {
  threshold: "概率阈值", recalled: "原消息已撤回",
  recalled_during_check: "判断期间撤回", judge_error: "判断服务故障",
  disabled: "功能旁路",
};
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
async function api(path, body) {
  const response = await fetch(`/api/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    if (response.status === 401) logout();
    throw new Error(`请求失败 (${response.status})，请检查令牌、输入或服务状态。`);
  }
  return response.json();
}
function draft() {
  return {
    pre_prompt: $("pre-prompt").value, post_prompt: $("post-prompt").value,
    include_persona: $("include-persona").checked,
  };
}
function fill(policy) {
  $("pre-prompt").value = policy.pre_prompt;
  $("post-prompt").value = policy.post_prompt;
  $("include-persona").checked = policy.include_persona;
}
async function status() {
  const data = await api("status");
  $("running").textContent = data.enabled ? "接话守卫已启用" : "旁路 · 总开关已关闭";
  $("model").textContent = `${data.model} / 阈值 ${data.threshold} / ${data.api_configured ? "API 已配置" : "未配置 API 密钥"}`;
  $("decisions").textContent = data.decisions;
  $("blocked").textContent = data.blocked;
  $("storage").textContent = data.history_error ? "历史存储异常，请检查磁盘" : "历史存储无已知异常";
  $("switches").replaceChildren();
  for (const [key, value] of Object.entries(data.switches)) {
    const item = document.createElement("div"); item.className = "switch-item";
    const name = document.createElement("span"); name.textContent = names[key] || key;
    const state = document.createElement("span"); state.textContent = value ? "开启" : "关闭";
    state.className = value ? "on" : "off"; item.append(name, state); $("switches").append(item);
  }
  const selected = $("preview-session").value;
  $("preview-session").replaceChildren(new Option("无会话 / 空人格", ""));
  for (const session of data.sessions) {
    $("preview-session").add(new Option(`${session.persona_id || "未解析人格"} · ${session.id}`, session.id));
  }
  if (data.sessions.some((session) => session.id === selected)) $("preview-session").value = selected;
}
async function history(reset = true) {
  const data = await api(`history?before=${reset ? 0 : cursor}`);
  if (reset) $("history-list").replaceChildren();
  for (const record of data.records) {
    const detail = document.createElement("details"); detail.className = "record";
    const summary = document.createElement("summary");
    const title = document.createElement("span");
    title.textContent = `${record.allowed ? "放行" : "拦截"} · ${record.stage} · ${reasons[record.reason] || record.reason}`;
    const meta = document.createElement("span"); meta.className = "muted";
    meta.textContent = `${new Date(record.timestamp * 1000).toLocaleString()} · ${record.probability === null ? "规则" : `P=${record.probability}`} · ${record.elapsed_ms}ms`;
    const content = document.createElement("pre"); content.textContent = JSON.stringify(record, null, 2);
    summary.append(title, meta); detail.append(summary, content); $("history-list").append(detail);
  }
  if (!$("history-list").children.length) {
    const empty = document.createElement("p"); empty.className = "empty";
    empty.textContent = "暂无判断记录。检查总开关，或先运行一次连接测试。"; $("history-list").append(empty);
  }
  cursor = data.records.length ? data.records[data.records.length - 1].id : 0;
  $("more-history").hidden = data.records.length < 30;
}
async function action(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
}
function logout() {
  token = ""; $("token").value = ""; $("workspace").hidden = true;
  $("login").hidden = false; $("connection").textContent = "未连接";
  $("history-list").replaceChildren(); $("preview-output").textContent = "";
  $("probe-output").textContent = ""; $("pre-prompt").value = ""; $("post-prompt").value = "";
  defaults = null;
}
$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    token = $("token").value.trim();
    await status();
    const data = await api("policy"); defaults = data.defaults; fill(data.policy);
    await history();
    $("token").value = ""; $("workspace").hidden = false; $("login").hidden = true;
    $("connection").textContent = "已安全连接"; notice("控制台已连接。");
  });
});
$("logout").onclick = () => { logout(); notice("已清除访问令牌。"); };
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll(".tab-panel").forEach((panel) => { panel.hidden = panel.id !== button.dataset.tab; });
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  };
});
document.querySelectorAll(".insert").forEach((button) => {
  button.onclick = () => {
    const editor = $(button.dataset.target);
    if (editor.value.includes("{{persona}}")) { notice("这个模板已有 {{persona}}，无需重复插入。"); return; }
    editor.setRangeText("{{persona}}", editor.selectionStart, editor.selectionEnd, "end");
    editor.focus(); notice("已插入变量；如需传入人格，请勾选上方开关。");
  };
});
$("save-policy").onclick = () => action($("save-policy"), async () => {
  await api("policy", draft()); notice("规则已保存。下一次判断立即生效，旧判断保留原始快照。");
});
$("reset-policy").onclick = () => { if (defaults) fill(defaults); notice("已填入默认模板，尚未保存。"); };
$("preview").onclick = () => action($("preview"), async () => {
  const data = await api("preview", { policy: draft(), session: $("preview-session").value });
  $("preview-note").textContent = `${data.note} 人格状态：${data.persona_status} ${data.persona_id}`;
  $("preview-output").textContent = `【接话判断】\n${data.pre}\n\n【发送复核】\n${data.post}`;
});
$("probe").onclick = () => action($("probe"), async () => {
  const data = await api("probe", {}); $("probe-output").textContent = JSON.stringify(data, null, 2);
  await status(); notice(data.last_decision.reason === "judge_error" ? "连接测试失败，请查看历史记录中的错误码。" : "连接测试完成。", data.last_decision.reason === "judge_error");
});
$("refresh-status").onclick = () => action($("refresh-status"), status);
$("refresh-history").onclick = () => action($("refresh-history"), () => history());
$("more-history").onclick = () => action($("more-history"), () => history(false));
