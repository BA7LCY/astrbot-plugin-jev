"""AstrBot 内嵌 Pages 的后端 handler；不自行监听端口，鉴权由宿主登录态负责。"""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import fields
from pathlib import Path

from astrbot.api.web import error_response, json_response, request

from .config import Settings
from .engine import Engine, Message
from .policy import MODULES, Policy
from .policy_defaults import default_policy_text


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

    def policy_text(policy: Policy) -> dict[str, str]:
        """把当前配置转成控制台编辑器使用的三段 JSON 文本。"""
        return {
            name: json.dumps(getattr(policy, name), ensure_ascii=False, indent=2)
            for name in MODULES
        }

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
                "api_base": cfg.api_base,
                "model": cfg.model,

                "switches": {
                    field.name: getattr(cfg, field.name)
                    for field in fields(Settings)
                    if type(getattr(cfg, field.name)) is bool
                },
                "decisions": current.decisions,
                "allowed": current.allowed,
                "blocked": current.blocked,
                "history_error": current.history_error,
                "last_decision": current.last_decision,
                "recall_support": "OneBot v11 group_recall / friend_recall",
                "scope": "AstrBot pipeline replies; direct tool/plugin sends excluded",
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
            {
                "policy_text": policy_text(current.policy),
                "defaults_text": default_policy_text(),
            }
        )

    async def save_policy():
        current = state.get("engine")
        if current is None:
            return error_response("插件未就绪", status_code=503)
        if policy_path is None:
            return error_response("偏好存储不可用", status_code=409)
        try:
            body = await request.json(default={})
            if not isinstance(body, dict) or set(body) != {"policy_text"}:
                raise ValueError("请求格式不正确")
            updated = Policy.parse_text(body["policy_text"])
        except (ValueError, TypeError):
            return error_response("配置 JSON 格式不合法", status_code=400)
        async with policy_lock:
            await asyncio.to_thread(updated.save, policy_path)
            current.policy = updated
        return json_response({"policy_text": policy_text(updated)})

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
        "probe": (guard(probe), ["POST"]),
    }
