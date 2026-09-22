"""AstrBot 事件兼容层；核心仅接收普通数据。"""

import json
import uuid

from astrbot.api.event import filter
from astrbot.core.persona_error_reply import resolve_event_conversation_persona_id
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter

from .engine import Engine, Message


class IngressFilter(filter.CustomFilter):
    adapter = None

    def filter(self, event, cfg) -> bool:
        """在唤醒检查阶段捕获撤回通知并选择受管事件。

        Args:
            event: 宿主消息事件。
            cfg: 宿主会话配置。

        Returns:
            是否安排异步接话判断；不在白名单范围内或为通知时返回 False。
        """
        adapter = self.adapter
        if adapter is None or not adapter.engine.active(event.is_private_chat()):
            return False
        if not adapter.in_scope(event):
            return False
        raw = event.message_obj.raw_message
        if isinstance(raw, dict) and raw.get("post_type") == "notice":
            if adapter.engine.settings.recall_enabled and raw.get("notice_type") in (
                "group_recall",
                "friend_recall",
            ):
                adapter.engine.recall(
                    adapter.session_key(event), str(raw.get("message_id", ""))
                )
            return False
        if isinstance(raw, dict) and raw.get("post_type", "message") != "message":
            return False
        if str(event.get_self_id()) == str(event.get_sender_id()):
            return False
        if not event.is_private_chat() and not event.get_group_id():
            return False
        cfg = adapter.engine.settings
        if cfg.post_check_enabled or cfg.recall_enabled:
            # 必须早于 AgentRequestSubStage 读取 enable_streaming。
            event.set_extra(
                "jev_original_streaming", event.get_extra("enable_streaming")
            )
            event.set_extra("enable_streaming", False)
        return True


class AstrBotAdapter:
    def __init__(self, engine: Engine, context=None):
        self.engine = engine
        self.context = context

    @staticmethod
    def session_key(event) -> str:
        """构建群级上下文键，不受宿主 unique_session 按成员隔离影响。

        Args:
            event: 消息或撤回事件。

        Returns:
            平台实例、机器人、会话类型和目标共同构成的唯一键。
        """
        return json.dumps(
            [
                event.get_platform_id(),
                event.get_self_id(),
                "private" if event.is_private_chat() else "group",
                event.get_sender_id()
                if event.is_private_chat()
                else event.get_group_id(),
            ],
            ensure_ascii=False,
        )

    @staticmethod
    def listed(event, ids: list[str]) -> bool:
        """按宿主同款规则匹配：会话 unified_msg_origin 或群号命中任意一项。

        Args:
            event: 消息事件。
            ids: 已清洗的白名单。

        Returns:
            是否命中白名单。
        """
        return (
            event.unified_msg_origin in ids or str(event.get_group_id()).strip() in ids
        )

    def in_scope(self, event) -> bool:
        """核对宿主两层白名单，决定是否允许接管该会话。

        Args:
            event: 消息事件。

        Returns:
            是否可接管；读不到会话配置时不接管。
        """
        if self.context is None or event.get_platform_name() == "webchat":
            return True
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
        except Exception:
            return False
        settings = cfg.get("platform_settings", {})
        ids = [
            str(i).strip() for i in settings.get("id_whitelist", []) if str(i).strip()
        ]
        admin_exempt = event.is_admin() and (
            settings.get("wl_ignore_admin_on_group")
            if not event.is_private_chat()
            else settings.get("wl_ignore_admin_on_friend")
        )
        if settings.get("enable_id_white_list") and ids and not admin_exempt:
            if not self.listed(event, ids):
                return False
        active_reply = cfg.get("provider_ltm_settings", {}).get("active_reply", {})
        ids = [
            str(i).strip() for i in active_reply.get("whitelist", []) if str(i).strip()
        ]
        if ids and not event.is_private_chat():
            return self.listed(event, ids)
        return True

    @staticmethod
    def restore_streaming(event) -> None:
        """Jev 不产生回复时，把流式输出交还给宿主原配置。

        Args:
            event: 消息事件。
        """
        event.set_extra("enable_streaming", event.get_extra("jev_original_streaming"))

    async def receive(self, event) -> None:
        """接管普通聊天；已匹配的插件命令默认旁路。

        Args:
            event: 已通过宿主白名单范围检查的消息。
        """
        if not self.engine.active(event.is_private_chat()):
            return
        if self.engine.settings.bypass_commands:
            handlers = event.get_extra("activated_handlers", [])
            if any(
                isinstance(item, (CommandFilter, CommandGroupFilter))
                for handler in handlers
                for item in handler.event_filters
            ):
                event.set_extra("jev_bypass", True)
                self.restore_streaming(event)
                return
        message = Message(
            session=self.session_key(event),
            message_id=str(event.message_obj.message_id),
            sender=str(event.get_sender_id()),
            text=event.message_str,
            private=event.is_private_chat(),
            addressed=bool(event.is_at_or_wake_command),
        )
        self.engine.observe(message)
        event.set_extra("jev_message", message)
        if self.engine.policy.uses_persona:
            await self.resolve_persona(event, message)
        allowed = await self.engine.decide("pre", message)
        event.set_extra("jev_pre_allowed", allowed)
        if not allowed:
            # stop_event 会连带跳过内置的群聊上下文记录，这里只掐断默认 LLM 链路。
            event.set_extra("jev_rejected", True)
            self.restore_streaming(event)
            event.should_call_llm(True)
            event.is_at_or_wake_command = True
        elif self.engine.settings.pre_check_enabled:
            event.is_at_or_wake_command = True
            event.is_wake = True

    async def resolve_persona(self, event, message: Message) -> None:
        """复用宿主人格选择逻辑，含会话覆盖和显式禁用。

        Args:
            event: 当前消息事件。
            message: 待填充人格快照的领域消息。
        """
        try:
            persona_id = await resolve_event_conversation_persona_id(
                event, self.context.conversation_manager
            )
            (
                selected_id,
                persona,
                _,
                _,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=persona_id,
                platform_name=event.get_platform_name(),
                provider_settings=self.context.get_config(event.unified_msg_origin).get(
                    "provider_settings", {}
                ),
            )
            message.persona_id = str(selected_id or "")
            message.persona = str((persona or {}).get("prompt", ""))[:6000]
            message.persona_status = "resolved" if persona else "none"
        except Exception:
            message.persona_status = "unavailable"

    async def before_llm(self, event) -> None:
        """确保撤回发生在排队期间时不会继续调用回复模型。

        Args:
            event: 正要发送 LLM 请求的宿主事件。
        """
        message = event.get_extra("jev_message")
        if (
            message is None
            or event.get_extra("jev_bypass")
            or event.get_extra("jev_rejected")
        ):
            return
        if not self.engine.active(message.private):
            return
        if self.engine.policy.uses_persona:
            await self.resolve_persona(event, message)
        if not await self.engine.decide("final", message):
            event.stop_event()
            return
        event.set_extra("jev_generation", True)

    async def before_send(self, event) -> None:
        """在宿主装饰/发送阶段复核生成文本，禁止发送被撤回的回复。

        Args:
            event: 带有最终结果的宿主消息。
        """
        message = event.get_extra("jev_message")
        result = event.get_result()
        if (
            message is None
            or event.get_extra("jev_bypass")
            or not event.get_extra("jev_generation")
            or result is None
            or not result.chain
        ):
            return
        if not self.engine.active(message.private):
            return
        if self.engine.policy.uses_persona:
            await self.resolve_persona(event, message)
        candidate = event.get_extra("jev_candidate")
        if candidate is None:
            candidate = result.get_plain_text(with_other_comps_mark=True)
        if not event.get_extra("jev_post_checked"):
            allowed = await self.engine.decide("post", message, candidate)
            event.set_extra("jev_post_checked", True)
            event.set_extra("jev_post_allowed", allowed)
        else:
            allowed = event.get_extra("jev_post_allowed", True)
        if allowed:
            allowed = await self.engine.decide("final", message, candidate)
        if not allowed:
            result.chain.clear()
            event.stop_event()
        elif not event.get_extra("jev_send_guard"):
            original_send = event.send

            async def guarded_send(chain):
                # 装饰后的 TTS、图片转换和分段延迟期间仍可能发生撤回。
                if not await self.engine.decide("final", message, candidate):
                    event.set_extra("jev_delivery_blocked", True)
                    event.stop_event()
                    return
                return await original_send(chain)

            event.send = guarded_send
            event.set_extra("jev_send_guard", True)

    def after_sent(self, event) -> None:
        """只把实际发送完成的机器人回复加入上下文。

        Args:
            event: 发送完成的宿主事件。
        """
        message = event.get_extra("jev_message")
        result = event.get_result()
        if (
            message is None
            or not self.engine.active(message.private)
            or not event.get_extra("jev_generation")
            or result is None
            or not result.chain
            or event.get_extra("jev_context_sent")
            or event.get_extra("jev_delivery_blocked")
        ):
            return
        self.engine.observe(
            Message(
                message.session,
                f"bot:{uuid.uuid4().hex}",
                str(event.get_self_id()),
                event.get_extra("jev_candidate")
                or result.get_plain_text(with_other_comps_mark=True),
                private=message.private,
                role="assistant",
            )
        )
        event.set_extra("jev_context_sent", True)
