"use strict";
const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
let defaults = null;
let defaultQuestions = {};
let cursor = 0;
const names = {
  enabled: "插件总开关", group_enabled: "群聊", private_enabled: "私聊",
  pre_check_enabled: "接话判断", post_check_enabled: "发送复核",
  recall_enabled: "撤回拦截", history_enabled: "历史记录",
  bypass_commands: "命令旁路", bypass_addressed: "@跳过判断", fail_open: "故障放行",
  busy_lock_enabled: "回复中按住",
  burst_merge_enabled: "合并窗口", dry_run_enabled: "影子模式",
  custom_gates_enabled: "自定义闸门数值",
};
const reasons = {
  threshold: "概率阈值", recalled: "原消息已撤回", addressed_bypass: "被@直接放行",
  recalled_during_check: "判断期间撤回", judge_error: "判断服务故障",
  disabled: "功能旁路", missing_judgment: "返回缺题",
  intrusive: "会打断别人", topic_continuation: "接着刚才的话", implicit_address: "在跟机器人说话",
  welcome_contribution: "值得插一句", uncertain_or_low_value: "不确定或不值得",
  inappropriate_draft: "草稿不合适", redundant_draft: "草稿重复", review_passed: "复核通过",
  stale_after_new_messages: "新消息让回复过时", freshness_passed: "时效通过",
};
const stages = { pre: "接话", post: "复核", final: "时效", probe: "探针" };
const labels = {
  addressed: "是叫我", continuation: "续话", worthwhile: "值得", intrusive: "打扰",
  appropriate: "草稿合适", redundant: "草稿重复", still_fits: "还没过时",
};
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
const QUESTION_META = [
  ["addressed", "接话 · 是不是在叫我"], ["continuation", "接话 · 是不是接着刚才的话茬"],
  ["worthwhile", "接话 · 值得开口吗"], ["intrusive", "接话 · 会不会打断别人"],
  ["still_fits", "复核 · 草稿还合时宜吗"], ["appropriate", "复核 · 草稿合适吗"],
  ["redundant", "复核 · 草稿重复吗"],
];
function buildOverrides() {
  const box = $("overrides");
  for (const [key, label] of QUESTION_META) {
    const wrap = document.createElement("div"); wrap.className = "override";
    const title = document.createElement("label"); title.textContent = label;
    const ta = document.createElement("textarea"); ta.rows = 5; ta.dataset.q = key; ta.className = "ov-instructions";
    const preset = defaultQuestions[key];
    ta.value = preset ? preset.instructions : "";
    ta.placeholder = "这里是内置题面原文；改成别的才会作为覆写保存";
    const yes = document.createElement("input"); yes.className = "ov-true"; yes.dataset.q = key;
    yes.value = (preset && preset.criteria.true) || "";
    yes.placeholder = "算作「是」的标准"
    const no = document.createElement("input"); no.className = "ov-false"; no.dataset.q = key;
    no.value = (preset && preset.criteria.false) || "";
    no.placeholder = "算作「否」的标准";
    wrap.append(title, ta, yes, no); box.append(wrap);
  }
}
function draft() {
  const overrides = {};
  for (const ta of document.querySelectorAll(".ov-instructions")) {
    const key = ta.dataset.q;
    let instructions = ta.value.trim();
    const preset = defaultQuestions[key] || {};
    if (instructions === ((preset.instructions) || "").trim()) instructions = "";
    const yes = document.querySelector(`.ov-true[data-q="${key}"]`).value.trim();
    const no = document.querySelector(`.ov-false[data-q="${key}"]`).value.trim();
    const entry = {};
    if (instructions) entry.instructions = instructions;
    const criteria = {};
    if (yes && yes !== ((preset.criteria || {}).true || "").trim()) criteria.true = yes;
    if (no && no !== ((preset.criteria || {}).false || "").trim()) criteria.false = no;
    if (Object.keys(criteria).length) entry.criteria = criteria;
    if (Object.keys(entry).length) overrides[key] = entry;
  }
  return { guidance: $("guidance").value, overrides };
}
function fill(policy) {
  $("guidance").value = policy.guidance || "";
  const overrides = policy.overrides || {};
  for (const [key] of QUESTION_META) {
    const entry = overrides[key] || {};
    const preset = defaultQuestions[key] || {};
    document.querySelector(`.ov-instructions[data-q="${key}"]`).value = entry.instructions || preset.instructions || "";
    document.querySelector(`.ov-true[data-q="${key}"]`).value = (entry.criteria && entry.criteria.true) || (preset.criteria && preset.criteria.true) || "";
    document.querySelector(`.ov-false[data-q="${key}"]`).value = (entry.criteria && entry.criteria.false) || (preset.criteria && preset.criteria.false) || "";
  }
}
async function status() {
  const data = await bridge.apiGet("status");
  $("running").textContent = data.enabled ? "接话守卫已启用" : "旁路 · 总开关已关闭";
  $("model").textContent = `${data.model} / 接口 ${data.api_base.replace(/^https:\/\//, "")} / ${data.api_configured ? "API 已配置" : "未配置 API 密钥"}`;
  $("decisions").textContent = data.decisions;
  $("allowed").textContent = data.allowed;
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
  const data = await bridge.apiGet("history", { before: reset ? 0 : cursor });
  if (reset) $("history-list").replaceChildren();
  for (const record of data.records) {
    const detail = document.createElement("details"); detail.className = "record";
    const summary = document.createElement("summary");
    const title = document.createElement("span");
    const shadow = record.dry_run ? "影子 · " : "";
    title.textContent = `${shadow}${record.allowed ? "放行" : "拦截"} · ${stages[record.stage] || record.stage} · ${reasons[record.reason] || record.reason}`;
    const meta = document.createElement("span"); meta.className = "muted";
    const scores = record.values == null ? "规则" : Object.entries(record.values).map(([k, v]) => `${labels[k] || k}=${v}`).join("  ·  ");
    meta.textContent = `${new Date(record.timestamp * 1000).toLocaleString()} · ${scores} · ${record.elapsed_ms}ms`;
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
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll(".tab-panel").forEach((panel) => { panel.hidden = panel.id !== button.dataset.tab; });
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  };
});
document.querySelectorAll(".insert").forEach((button) => {
  button.onclick = () => {
    const editor = $(button.dataset.target);
    if (editor.value.includes("{{persona}}")) { notice("这里已有 {{persona}}，无需重复插入。"); return; }
    editor.setRangeText("{{persona}}", editor.selectionStart, editor.selectionEnd, "end");
    editor.focus(); notice("已插入 {{persona}}；保存后偏好会带上当前会话人格。");
  };
});

$("export-policy").onclick = () => action($("export-policy"), async () => {
  await navigator.clipboard.writeText(JSON.stringify(draft(), null, 2));
  notice("已把当前规则（含覆写）复制到剪贴板，可发给别人或存起来。");
});
$("import-fill").onclick = () => {
  try {
    fill(JSON.parse($("import-text").value));
    notice("已填入下方编辑区，确认无误后点「保存」。");
  } catch {
    notice("JSON 无法解析，检查是否完整复制。", true);
  }
};
$("save-policy").onclick = () => action($("save-policy"), async () => {
  await bridge.apiPost("policy/save", draft()); notice("偏好已保存。下一次判断立即生效，旧判断保留原始快照。");
});
$("reset-policy").onclick = () => { if (defaults) fill(defaults); notice("已清空偏好，尚未保存。"); };
$("preview").onclick = () => action($("preview"), async () => {
  const data = await bridge.apiPost("preview", { policy: draft(), session: $("preview-session").value });
  $("preview-note").textContent = `${data.note} 人格状态：${data.persona_status} ${data.persona_id} / ${data.persona_chars} 字符`;
  $("preview-output").textContent = `【聊天偏好】\n${data.guidance}\n\n【拼进题目后的接话题样例】\n${data.sample}`;
});
$("probe").onclick = () => action($("probe"), async () => {
  const data = await bridge.apiPost("probe", {}); $("probe-output").textContent = JSON.stringify(data, null, 2);
  await status(); notice(data.last_decision.reason === "judge_error" ? "连接测试失败，请查看历史记录中的错误码。" : "连接测试完成。", data.last_decision.reason === "judge_error");
});
$("refresh-status").onclick = () => action($("refresh-status"), status);
$("refresh-history").onclick = () => action($("refresh-history"), async () => { await status(); await history(); });
$("more-history").onclick = () => action($("more-history"), () => history(false));

async function connect() {
  try {
  await bridge.ready();
  await status();
  const questions = await bridge.apiGet("questions");
  defaultQuestions = questions.questions || {};
  buildOverrides();
  const data = await bridge.apiGet("policy");
    defaults = data.defaults;
    fill(data.policy);
    await history();
    $("connection").textContent = "已连接";
    notice("控制台已就绪。");
  } catch (error) {
    $("connection").textContent = "不可用";
    notice(error.message, true);
  }
}
connect();
