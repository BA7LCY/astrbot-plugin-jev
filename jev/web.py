"""仅绑定回环地址、使用 Bearer 鉴权的运行检查接口。"""

import asyncio
import hmac
import sqlite3
from dataclasses import asdict, fields
from pathlib import Path

from aiohttp import web

from .config import Settings
from .engine import Engine, Message
from .policy import Policy


def create_app(engine: Engine, policy_path: Path | None = None) -> web.Application:
    """构建可独立测试的 WebUI，不暴露任何配置密钥。

    Args:
        engine: 注入的核心服务。
        policy_path: 模板持久化路径；未指定时禁止修改。

    Returns:
        带运行检查、手动探针和历史分页的 aiohttp 应用。
    """
    probe_lock = asyncio.Lock()
    policy_lock = asyncio.Lock()
    assets = Path(__file__).resolve().parent.parent / "webui"

    @web.middleware
    async def protect(request, handler):
        if request.path.startswith("/api/"):
            expected = f"Bearer {engine.settings.webui_token}"
            supplied = request.headers.get("Authorization", "")
            if not engine.settings.webui_token or not hmac.compare_digest(
                supplied.encode(), expected.encode()
            ):
                raise web.HTTPUnauthorized()
            if request.method != "GET" and request.headers.get("Origin"):
                if request.headers["Origin"] != f"{request.scheme}://{request.host}":
                    raise web.HTTPForbidden()
        try:
            response = await handler(request)
        except (OSError, sqlite3.Error):
            response = web.json_response({"error": "storage_unavailable"}, status=503)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; "
                "script-src 'self'; style-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    async def status(request):
        cfg = engine.settings
        return web.json_response(
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
                "decisions": engine.decisions,
                "blocked": engine.blocked,
                "history_error": engine.history_error,
                "last_decision": engine.last_decision,
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
                    for key, items in engine.contexts.items()
                    if items
                ],
            }
        )

    async def history(request):
        try:
            before = int(request.query.get("before", "0"))
            if before < 0:
                raise ValueError
        except ValueError:
            raise web.HTTPBadRequest() from None
        rows = (
            await asyncio.to_thread(engine.history.recent, before)
            if engine.history and engine.settings.history_enabled
            else []
        )
        return web.json_response({"records": rows})

    async def probe(request):
        if probe_lock.locked():
            return web.json_response({"error": "probe_busy"}, status=429)
        async with probe_lock:
            message = Message(
                "diagnostic",
                "probe",
                "synthetic-user",
                "你好，机器人，能和我打个招呼吗？",
                addressed=True,
            )
            allowed = await engine.decide("probe", message)
            return web.json_response(
                {"allowed": allowed, "last_decision": engine.last_decision}
            )

    async def policy(request):
        if request.method == "GET":
            return web.json_response(
                {"policy": asdict(engine.policy), "defaults": asdict(Policy())}
            )
        if policy_path is None:
            raise web.HTTPForbidden()
        try:
            updated = Policy.parse(await request.json())
        except (ValueError, TypeError):
            return web.json_response({"error": "模板格式或变量不合法"}, status=400)
        async with policy_lock:
            await asyncio.to_thread(updated.save, policy_path)
            engine.policy = updated
        return web.json_response({"policy": asdict(updated)})

    async def preview(request):
        try:
            body = await request.json()
            draft = Policy.parse(body["policy"])
            session_id = body.get("session", "")
            if not isinstance(session_id, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "预览参数不合法"}, status=400)
        messages = engine.contexts.get(session_id, ())
        message = next(
            (item for item in reversed(messages) if item.role == "user"), None
        )
        persona_text = message.persona if message else ""
        return web.json_response(
            {
                "pre": draft.render(
                    "pre", engine.settings.bot_description, persona_text
                ),
                "post": draft.render(
                    "post", engine.settings.bot_description, persona_text
                ),
                "persona_id": message.persona_id if message else "",
                "persona_status": message.persona_status if message else "no_session",
                "note": "预览使用所选会话最近的已解析快照；真实判断时重新读取当前人格。",
            }
        )

    async def asset(request):
        name = request.match_info.get("name", "index.html")
        if name not in ("index.html", "app.js", "style.css"):
            raise web.HTTPNotFound()
        return web.FileResponse(assets / name)

    app = web.Application(middlewares=[protect], client_max_size=65536)
    app.router.add_get("/", asset)
    app.router.add_get("/assets/{name}", asset)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/history", history)
    app.router.add_post("/api/probe", probe)
    app.router.add_get("/api/policy", policy)
    app.router.add_post("/api/policy", policy)
    app.router.add_post("/api/preview", preview)
    return app
