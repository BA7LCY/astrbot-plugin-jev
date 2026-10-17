"""AstrBot 插件入口，生命周期与事件注册集中于此。"""

import asyncio
import json
from pathlib import Path

import aiohttp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .jev.astrbot_adapter import AstrBotAdapter, IngressFilter
from .jev.client import JevClient
from .jev.config import Settings
from .jev.engine import Engine
from .jev.history import History
from .jev.policy import Policy
from .jev.web import build_handlers

PLUGIN_NAME = "astrbot_plugin_jev"


class JevPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.settings = Settings.load(dict(config))
        self.session = None
        self.engine_slot: dict[str, Engine | None] = {"engine": None}
        self.adapter = None

    async def initialize(self):
        try:
            cfg = self.settings
            data_path = Path(get_astrbot_data_path()) / "plugin_data" / "jev"
            policy_path = data_path / "policy.json"
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=cfg.timeout_seconds)
            )
            history = None
            if cfg.history_enabled:
                history = await asyncio.to_thread(
                    History,
                    data_path / "history.db",
                    cfg.history_limit,
                )
            engine = Engine(cfg, JevClient(cfg, self.session), history)
            if policy_path.exists():
                try:
                    engine.policy = Policy.parse(
                        json.loads(policy_path.read_text(encoding="utf-8"))
                    )
                except (ValueError, json.JSONDecodeError) as error:
                    # 旧版本模板不能让插件起不来。
                    self.logger.warning(f"Ignored invalid policy.json: {error}")
            self.adapter = AstrBotAdapter(engine, self.context)
            self.engine_slot["engine"] = engine
            for endpoint, (handler, methods) in build_handlers(
                self.engine_slot, policy_path
            ).items():
                self.context.register_web_api(
                    f"/{PLUGIN_NAME}/{endpoint}",
                    handler,
                    methods,
                    f"Jev {endpoint}",
                )
            IngressFilter.adapter = self.adapter
            self.logger.info("Jev plugin initialized")
        except Exception:
            await self.terminate()
            raise

    @filter.custom_filter(IngressFilter, priority=10000)
    async def receive(self, event: AstrMessageEvent):
        if self.adapter:
            await self.adapter.receive(event)

    @filter.on_llm_request(priority=10000)
    async def before_llm(self, event: AstrMessageEvent, request):
        if self.adapter:
            await self.adapter.before_llm(event)

    @filter.on_llm_response(priority=-10000)
    async def capture_response(self, event: AstrMessageEvent, response):
        if response is not None and event.get_extra("jev_generation"):
            event.set_extra("jev_candidate", response.completion_text or "")

    @filter.on_decorating_result(priority=-10000)
    async def before_send(self, event: AstrMessageEvent):
        if self.adapter:
            await self.adapter.before_send(event)

    @filter.after_message_sent()
    async def after_sent(self, event: AstrMessageEvent):
        if self.adapter:
            self.adapter.after_sent(event)

    async def terminate(self):
        if IngressFilter.adapter is self.adapter:
            IngressFilter.adapter = None
        self.adapter = None
        self.engine_slot["engine"] = None
        if self.session:
            await self.session.close()
            self.session = None
