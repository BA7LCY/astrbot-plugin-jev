from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.event import MessageEventResult
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.filter.command import CommandFilter
from test_core import Judge

from jev.astrbot_adapter import AstrBotAdapter, IngressFilter
from jev.config import Settings
from jev.engine import Engine
from jev.policy import Policy


class Event(AstrMessageEvent):
    async def send(self, chain):
        self.sent.append(chain)


def event(private=False, raw=None, message_id="1"):
    obj = AstrBotMessage()
    obj.type = MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
    obj.self_id = "bot"
    obj.sender = MessageMember("user", "用户")
    obj.group_id = "" if private else "group"
    obj.message_id = message_id
    obj.message_str = "hello bot"
    obj.message = [Plain("hello bot")]
    obj.raw_message = raw or {"post_type": "message"}
    result = Event(
        obj.message_str,
        obj,
        PlatformMetadata("aiocqhttp", "test", "platform-a"),
        "per-user-session",
    )
    result.sent = []
    return result


@pytest.fixture
def adapter():
    engine = Engine(Settings(enabled=True, group_enabled=True), Judge(), None)
    result = AstrBotAdapter(engine)
    IngressFilter.adapter = result
    yield result
    IngressFilter.adapter = None


async def test_wake_and_send_flow(adapter):
    msg = event()
    assert IngressFilter().filter(msg, {})
    assert msg.get_extra("enable_streaming") is False
    await adapter.receive(msg)
    assert msg.is_at_or_wake_command
    await adapter.before_llm(msg)
    msg.set_result(MessageEventResult().message("response"))
    await adapter.before_send(msg)
    await msg.send(msg.get_result())
    adapter.after_sent(msg)
    assert len(msg.sent) == 1
    context = adapter.engine.contexts[adapter.session_key(msg)]
    assert context[-1].role == "assistant"
    assert [call[1] for call in adapter.engine.judge.calls] == ["pre", "post"]


async def test_recall_between_generation_and_send(adapter):
    msg = event()
    await adapter.receive(msg)
    await adapter.before_llm(msg)
    recall = event(
        raw={"post_type": "notice", "notice_type": "group_recall", "message_id": "1"},
        message_id="notice-id",
    )
    assert not IngressFilter().filter(recall, {})
    msg.set_result(MessageEventResult().message("must not send"))
    await adapter.before_send(msg)
    assert msg.is_stopped() and not msg.get_result().chain
    assert len(adapter.engine.judge.calls) == 1


async def test_recall_after_decoration_guard(adapter):
    msg = event()
    await adapter.receive(msg)
    await adapter.before_llm(msg)
    msg.set_result(MessageEventResult().message("reply"))
    await adapter.before_send(msg)
    adapter.engine.recall(adapter.session_key(msg), "1")
    await msg.send(msg.get_result())
    adapter.after_sent(msg)
    assert not msg.sent
    assert msg.get_extra("jev_delivery_blocked")
    assert len(adapter.engine.contexts[adapter.session_key(msg)]) == 1


async def test_recall_while_waiting_for_llm(adapter):
    msg = event()
    await adapter.receive(msg)
    adapter.engine.recall(adapter.session_key(msg), "1")
    await adapter.before_llm(msg)
    assert msg.is_stopped() and not msg.get_extra("jev_generation")


async def test_disabled_and_private_passthrough(adapter):
    msg = event(private=True)
    assert not IngressFilter().filter(msg, {})
    await adapter.receive(msg)
    assert not adapter.engine.judge.calls
    assert msg.get_extra("enable_streaming") is None
    adapter.engine.settings = replace(adapter.engine.settings, enabled=False)
    assert not IngressFilter().filter(event(), {})


async def test_pre_disabled_preserves_wakeup(adapter):
    adapter.engine.settings = replace(adapter.engine.settings, pre_check_enabled=False)
    msg = event()
    await adapter.receive(msg)
    assert not msg.is_at_or_wake_command
    assert not adapter.engine.judge.calls


async def test_rejection_only_blocks_default_llm_chain(adapter):
    """被拒消息不再 stop_event，内置的群聊上下文记录等后续处理照常运行。"""
    msg = event()
    msg.set_extra("enable_streaming", True)
    assert IngressFilter().filter(msg, {})
    adapter.engine.judge.probability = 0.1
    await adapter.receive(msg)
    assert msg.get_extra("jev_rejected")
    assert not msg.is_stopped()
    assert msg.call_llm is True
    assert msg.is_at_or_wake_command
    assert msg.get_extra("enable_streaming") is True
    await adapter.before_llm(msg)
    assert not msg.get_extra("jev_generation")
    assert len(adapter.engine.judge.calls) == 1


def host_config(platform=None, active=None, broken=False):
    def get_config(umo):
        if broken:
            raise RuntimeError("no session config")
        return {
            "platform_settings": platform or {},
            "provider_ltm_settings": {"active_reply": active or {}},
        }

    return SimpleNamespace(get_config=get_config)


async def test_host_id_whitelist_gates_takeover(adapter):
    msg = event()
    adapter.context = host_config({"enable_id_white_list": True, "id_whitelist": [""]})
    assert IngressFilter().filter(msg, {})
    adapter.context = host_config(
        {"enable_id_white_list": True, "id_whitelist": ["other"]}
    )
    assert not IngressFilter().filter(event(), {})
    adapter.context = host_config(
        {
            "enable_id_white_list": True,
            "id_whitelist": ["other"],
            "wl_ignore_admin_on_group": True,
        }
    )
    admin = event()
    admin.role = "admin"
    assert IngressFilter().filter(admin, {})
    adapter.context = host_config(broken=True)
    assert not IngressFilter().filter(event(), {})


async def test_active_reply_whitelist_gates_group_only(adapter):
    adapter.context = host_config(active={"whitelist": ["other-group"]})
    assert not IngressFilter().filter(event(), {})
    adapter.context = host_config(active={"whitelist": ["group"]})
    assert IngressFilter().filter(event(), {})
    adapter.engine.settings = replace(adapter.engine.settings, private_enabled=True)
    assert IngressFilter().filter(event(private=True), {})


async def test_registered_commands_bypass(adapter):
    msg = event()
    msg.set_extra("enable_streaming", True)
    IngressFilter().filter(msg, {})
    msg.set_extra(
        "activated_handlers",
        [SimpleNamespace(event_filters=[object.__new__(CommandFilter)])],
    )
    await adapter.receive(msg)
    await adapter.before_llm(msg)
    assert not adapter.engine.judge.calls
    assert msg.get_extra("jev_bypass")
    assert msg.get_extra("enable_streaming") is True


def persona_context(resolver):
    conversation = SimpleNamespace(persona_id="conversation-persona")
    return SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="conversation-id"),
            get_conversation=AsyncMock(return_value=conversation),
        ),
        persona_manager=SimpleNamespace(resolve_selected_persona=resolver),
        get_config=lambda umo: {
            "provider_settings": {"default_personality": "default"}
        },
    )


async def test_persona_resolves_session_selection(adapter):
    resolver = AsyncMock(
        return_value=("forced-persona", {"prompt": "这是会话指定人格"}, None, False)
    )
    adapter.context = persona_context(resolver)
    msg = event()
    await adapter.receive(msg)
    captured = msg.get_extra("jev_message")
    assert captured.persona_id == "forced-persona"
    assert "这是会话指定人格" in adapter.engine.judge.calls[0][2]
    assert (
        resolver.call_args.kwargs["conversation_persona_id"] == "conversation-persona"
    )
    assert resolver.call_args.kwargs["umo"] == msg.unified_msg_origin


async def test_template_without_persona_variable_skips_host(adapter):
    resolver = AsyncMock()
    adapter.context = persona_context(resolver)
    adapter.engine.policy = Policy("沉默规则", "发送？")
    msg = event()
    await adapter.receive(msg)
    resolver.assert_not_awaited()
    assert msg.get_extra("jev_message").persona_status == "unresolved"


async def test_unavailable_persona_degrades_without_blocking(adapter):
    msg = event()
    await adapter.receive(msg)
    captured = msg.get_extra("jev_message")
    assert captured.persona_status == "unavailable"
    assert not msg.is_stopped()
    assert adapter.engine.judge.calls


async def test_plugin_lifecycle_without_real_credentials():
    from astrbot_plugin_jev.main import JevPlugin

    registered = {}

    def register_web_api(route, handler, methods, desc=""):
        registered[route] = (handler, tuple(methods))

    plugin = JevPlugin(
        SimpleNamespace(register_web_api=register_web_api),
        {"enabled": False, "history_enabled": False},
    )
    await plugin.initialize()
    assert plugin.adapter is not None
    assert set(registered) == {
        f"/astrbot_plugin_jev/{name}"
        for name in ("status", "history", "policy", "policy/save", "preview", "probe")
    }
    await plugin.terminate()
    assert plugin.session is None
    assert plugin.engine_slot["engine"] is None
