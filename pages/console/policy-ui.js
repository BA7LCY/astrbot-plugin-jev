"use strict";

export const POLICY_MODULES = [
  { id: "addressed", title: "点名消息（@ / 唤醒词）" },
  { id: "intake", title: "普通接话（没点名）" },
  { id: "review", title: "发送前复核" },
];

let defaultsText = {};
let built = false;

export function setupPolicyEditors(defaults) {
  defaultsText = defaults || {};
  if (built) return;
  const box = document.getElementById("policy-modules");
  for (const module of POLICY_MODULES) {
    const section = document.createElement("article");
    section.className = "policy-module";

    const header = document.createElement("div");
    header.className = "policy-header";
    const title = document.createElement("h3");
    title.textContent = module.title;
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "secondary";
    restore.textContent = "恢复默认";
    restore.onclick = () => {
      document.getElementById(`policy-${module.id}`).value =
        defaultsText[module.id] || "";
    };
    header.append(title, restore);

    const editor = document.createElement("textarea");
    editor.id = `policy-${module.id}`;
    editor.rows = 18;
    editor.maxLength = 60000;
    editor.spellcheck = false;

    section.append(header, editor);
    box.append(section);
  }
  built = true;
}

export function fillPolicyEditors(policyText) {
  for (const module of POLICY_MODULES) {
    document.getElementById(`policy-${module.id}`).value =
      policyText[module.id] || defaultsText[module.id] || "";
  }
}

export function draftPolicy() {
  return {
    policy_text: Object.fromEntries(
      POLICY_MODULES.map((module) => [
        module.id,
        document.getElementById(`policy-${module.id}`).value,
      ]),
    ),
  };
}
