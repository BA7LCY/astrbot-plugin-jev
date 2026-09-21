"""AstrBot 内嵌 Pages 的后端 handler；不自行监听端口，鉴权由宿主登录态负责。"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import asdict, fields
from pathlib import Path

from astrbot.api.web import error_response, json_response, request

from .config import Settings
from .engine import Engine, Message
from .policy import Policy


def build_handlers(state: dict[str, Engine | None], policy_path: Path | None) -> dict:
    """构建控制台接口，页面在受限 iframe 内经 bridge 调用。

    Args:
        state: 可变引擎槽位；engine 为 None 表示插件尚未就绪。
        policy_path: 模板持久化路径；未指定时禁止修改。

    Returns:
        bridge endpoint 到 (handler, methods) 的映射。
    """
    probe_lock = asyncio.Lock()
    policy_lock = asyncio.Lock()

    def guard(handler: Callable[[], Awaitable]) -> Callable:
        """把存储故障转成不含敏感细节的状态码。

        Args:
            handler: 原始异步接口。

        Returns:
            带异常兜底的接口。
        """

        async def wrapped():
            try:
                return await handler()
            except (OSError, sqlite3.Error):
                return error_response("storage_unavailable", status_code=503)

        return wrapped

    async def status():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        cfg = current.settings
        return json_response(
            {
                "enabled": cfg.enabled,
                "api_configured": bool(cfg.api_key.strip()),
                "model": cfg.model,
                "threshold": cfg.threshold,
                "switches": {
                    field.name: getattr(cfg, field.name)
                    for field in fields(Settings)
                    if type(getattr(cfg, field.name)) is bool
                },
                "decisions": current.decisions,
                "blocked": current.blocked,
                "history_error": current.history_error,
                "last_decision": current.last_decision,
                "recall_support": "OneBot v11 group_recall / friend_recall",
                "scope": "AstrBot pipeline replies; direct tool/plugin sends excluded",
                "sessions": [
                    {
                        "id": key,
                        "persona_id": next(
                            (
                                item.persona_id
                                for item in reversed(items)
                                if item.role == "user"
                            ),
                            "",
                        ),
                    }
                    for key, items in current.contexts.items()
                    if items
                ],
            }
        )

    async def history():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        try:
            before = int(request.query.get("before", "0"))
            if before < 0:
                raise ValueError
        except ValueError:
            return error_response("before 必须是非负整数", status_code=400)
        rows = (
            current.history.recent(before)
            if current.history and current.settings.history_enabled
            else []
        )
        return json_response({"records": rows})

    async def get_policy():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        return json_response(
            {"policy": asdict(current.policy), "defaults": asdict(Policy())}
        )

    async def save_policy():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        if policy_path is None:
            return error_response("模板存储不可用", status_code=409)
        try:
            updated = Policy.parse(await request.json(default={}))
        except (ValueError, TypeError):
            return error_response("模板格式或变量不合法", status_code=400)
        async with policy_lock:
            await asyncio.to_thread(updated.save, policy_path)
            current.policy = updated
        return json_response({"policy": asdict(updated)})

    async def preview():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        try:
            body = await request.json(default={})
            draft = Policy.parse(body["policy"])
            session_id = body.get("session", "")
            if not isinstance(session_id, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return error_response("预览参数不合法", status_code=400)
        messages = current.contexts.get(session_id, ())
        message = next(
            (item for item in reversed(messages) if item.role == "user"), None
        )
        persona_text = message.persona if message else ""
        if not draft.uses_persona:
            note = "草稿模板未写 {{persona}}，人格不会进入请求。"
        elif not persona_text:
            note = (
                "草稿引用了 {{persona}}，但该会话快照没有人格文本；"
                "在群里发一条新消息后再预览即可看到真实取值。"
            )
        else:
            note = "预览使用所选会话最近的已解析快照；真实判断时重新读取当前人格。"
        return json_response(
            {
                "pre": draft.render("pre", persona_text),
                "post": draft.render("post", persona_text),
                "persona_id": message.persona_id if message else "",
                "persona_status": message.persona_status if message else "no_session",
                "persona_chars": len(persona_text),
                "note": note,
            }
        )

    async def probe():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        if probe_lock.locked():
            return error_response("已有探针在运行", status_code=429)
        async with probe_lock:
            allowed = await current.decide(
                "probe",
                Message(
                    "diagnostic",
                    "probe",
                    "synthetic-user",
                    "你好，机器人，能和我打个招呼吗？",
                    addressed=True,
                ),
            )
            return json_response(
                {"allowed": allowed, "last_decision": current.last_decision}
            )

    return {
        "status": (guard(status), ["GET"]),
        "history": (guard(history), ["GET"]),
        "policy": (guard(get_policy), ["GET"]),
        "policy/save": (guard(save_policy), ["POST"]),
        "preview": (guard(preview), ["POST"]),
        "probe": (guard(probe), ["POST"]),
    }
