import asyncio
import json
from dataclasses import asdict, replace

import pytest

from jev.client import JevError
from jev.config import Settings
from jev.engine import Engine, Message, render_line, render_scene
from jev.history import History
from jev.policy import Policy


class Judge:
    def __init__(self, probability=0.9, error=None, wait=None):
        self.probability = probability
        self.error = error
        self.wait = wait
        self.calls = []

    async def evaluate(self, state, stage, instructions):
        self.calls.append((state, stage, instructions))
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise self.error
        return {"probability": self.probability, "model": "fake-jev", "usage": {}}


@pytest.fixture
def setup_engine(tmp_path):
    def build(**overrides):
        cfg = Settings.load({"enabled": True, "group_enabled": True, **overrides})
        judge = Judge()
        store = History(tmp_path / "history.db", cfg.history_limit)
        return Engine(cfg, judge, store), judge, store

    return build


@pytest.mark.parametrize("probability,allowed", [(0.49, False), (0.5, True), (1, True)])
async def test_threshold_and_history(setup_engine, probability, allowed):
    engine, judge, store = setup_engine()
    judge.probability = probability
    msg = Message("group-a", "1", "user", "hello")
    engine.observe(msg)
    assert await engine.decide("pre", msg) is allowed
    record = store.recent()[0]
    assert record["allowed"] is allowed
    assert record["state"]["target"] == "成员1：hello〔群聊〕"
    assert record["instructions"] == judge.calls[0][2]
    assert record["model"] == "fake-jev"


@pytest.mark.parametrize("option", ["enabled", "group_enabled"])
async def test_disabled_scope_is_pure_bypass(setup_engine, option):
    engine, judge, store = setup_engine(**{option: False})
    assert await engine.decide("pre", Message("g", "1", "u", "x"))
    assert not judge.calls and not store.recent()


async def test_private_opt_in(setup_engine):
    engine, judge, _ = setup_engine()
    msg = Message("p", "1", "u", "x", private=True)
    assert await engine.decide("pre", msg)
    assert not judge.calls
    engine.settings = replace(engine.settings, private_enabled=True)
    await engine.decide("pre", msg)
    assert len(judge.calls) == 1


async def test_independent_switches(setup_engine):
    engine, judge, _ = setup_engine(pre_check_enabled=False)
    msg = Message("g", "1", "u", "x")
    assert await engine.decide("pre", msg)
    assert not judge.calls
    assert await engine.decide("post", msg, "reply")
    assert len(judge.calls) == 1
    engine.settings = replace(engine.settings, post_check_enabled=False)
    msg.recalled = True
    assert not await engine.decide("post", msg, "reply")
    engine.settings = replace(engine.settings, recall_enabled=False)
    assert await engine.decide("post", msg, "reply")
    assert len(judge.calls) == 1


@pytest.mark.parametrize("fail_open", [True, False])
async def test_error_policy_never_bypasses_recall(setup_engine, fail_open):
    engine, judge, store = setup_engine(fail_open=fail_open)
    judge.error = JevError("timeout")
    msg = Message("g", "1", "u", "x")
    assert await engine.decide("pre", msg) is fail_open
    assert store.recent()[0]["error"] == "timeout"
    msg.recalled = True
    assert not await engine.decide("post", msg, "reply")


async def test_recall_during_request_and_cross_session_isolation(setup_engine):
    engine, judge, store = setup_engine()
    judge.wait = asyncio.Event()
    msg = Message("g", "1", "u", "x")
    engine.observe(msg)
    task = asyncio.create_task(engine.decide("post", msg, "reply"))
    await asyncio.sleep(0)
    engine.recall("other", "1")
    assert not msg.recalled
    engine.recall("g", "1")
    judge.wait.set()
    assert not await task
    assert store.recent()[0]["reason"] == "recalled_during_check"


async def test_recall_during_audit_write(setup_engine):
    engine, _, _ = setup_engine()
    msg = Message("g", "1", "u", "x")
    original = engine.record

    async def record(value):
        await original(value)
        msg.recalled = True

    engine.record = record
    assert not await engine.decide("post", msg, "reply")
    assert engine.last_decision["reason"] == "recalled"


def test_snapshot_bounds_and_inflight_recall(setup_engine):
    engine, _, _ = setup_engine(text_limit=100)
    msg = Message("g", "1", "u", "x" * 200)
    engine.observe(msg)
    assert len(msg.text) == 100
    for number in range(129):
        engine.observe(Message(f"s{number}", str(number), "u", "n"))
    assert len(engine.personas) == 128
    assert "g" not in engine.personas
    engine.recall("g", "1")
    assert msg.recalled
    engine.recall("early", "3")
    later = Message("early", "3", "u", "x")
    engine.observe(later)
    assert later.recalled


async def test_history_disabled_still_judges(setup_engine):
    engine, judge, store = setup_engine(history_enabled=False)
    msg = Message("g", "1", "u", "x", conversation=["[小明/13:05]:  在吗"])
    assert await engine.decide("pre", msg)
    assert not store.recent()
    assert judge.calls[0][0]["conversation"] == ["[小明/13:05]:  在吗"]


async def test_own_reply_is_prepended_only_for_groups(setup_engine):
    engine, judge, _ = setup_engine()
    msg = Message("g", "1", "u", "question", conversation=["[小明/13:05]:  在吗"])
    engine.observe(msg)
    await engine.decide("pre", msg)
    assert judge.calls[-1][0]["conversation"] == ["[小明/13:05]:  在吗"]
    engine.observe(Message("g", "bot:1", "bot", "在的", role="assistant"))
    await engine.decide("post", msg, "answer")
    assert judge.calls[-1][0]["conversation"] == [
        "机器人：在的",
        "[小明/13:05]:  在吗",
    ]
    engine.settings = replace(engine.settings, private_enabled=True)
    await engine.decide(
        "pre", Message("p", "2", "u", "x", private=True, conversation=["对方：在吗"])
    )
    assert judge.calls[-1][0]["conversation"] == ["对方：在吗"]


async def test_persona_follows_template(setup_engine):
    engine, judge, store = setup_engine()
    msg = Message(
        "g",
        "1",
        "u",
        "x",
        persona="秘密人格",
        persona_id="custom",
        persona_status="resolved",
    )
    engine.observe(msg)
    engine.policy = Policy("沉默规则", "发送？")
    assert await engine.decide("pre", msg)
    assert "秘密人格" not in judge.calls[-1][2]
    assert "秘密人格" not in json.dumps(store.recent(), ensure_ascii=False)
    engine.policy = Policy("规则：{{persona}}", "发送？")
    assert await engine.decide("pre", msg)
    assert "秘密人格" in judge.calls[-1][2]
    assert "custom" not in json.dumps(store.recent()[0]["state"], ensure_ascii=False)
    msg.persona_status = "unavailable"
    assert await engine.decide("post", msg, "answer")
    assert store.recent()[0]["reason"] == "threshold"


def test_transcript_rendering_uses_readable_labels():
    names: dict[str, str] = {}
    people = [
        Message("g", "1", "alice", "先说话"),
        Message("g", "2", "bot", "收到", role="assistant"),
        Message("g", "3", "alice", "", recalled=True),
        Message("g", "4", "bob", "后说话"),
    ]
    assert [render_line(item, names) for item in people] == [
        "成员1：先说话",
        "机器人：收到",
        "成员1：（图片/表情等读不到的内容）〔这条已被撤回〕",
        "成员2：后说话",
    ]
    assert render_scene(people[0]) == "群聊"
    assert render_scene(Message("p", "5", "alice", "x", private=True)) == "私聊"
    assert (
        render_scene(Message("g", "6", "alice", "x", addressed=True))
        == "群聊，机器人被@了"
    )


def test_policy_validation_and_atomic_persistence(tmp_path):
    p = Policy("设定：{{persona}} / 结尾", "发送？")
    assert p.render("pre", "{{persona}}") == "设定：{{persona}} / 结尾"
    assert p.render("post", "人格") == "发送？"
    assert Policy("设定：{{persona}}", "发送？").render("pre", "人格") == "设定：人格"
    assert Policy("设定：{{persona}}", "发送？").uses_persona is True
    assert Policy("设定", "发送？").uses_persona is False
    path = tmp_path / "policy.json"
    p.save(path)
    Policy().save(path)
    assert Policy.parse(json.loads(path.read_text(encoding="utf-8"))) == Policy()
    assert json.loads(
        path.with_suffix(".json.bak").read_text(encoding="utf-8")
    ) == asdict(p)
    assert Policy.parse({**asdict(p), "pre_prompt": "{{persona}} 和 {{persona}}"})
    for data in [
        {**asdict(p), "pre_prompt": "{{unknown}}"},
        {**asdict(p), "pre_prompt": "{{bot_description}}"},
        {**asdict(p), "include_persona": True},
        {**asdict(p), "pre_prompt": ""},
    ]:
        with pytest.raises(ValueError):
            Policy.parse(data)


def test_history_retention_and_pagination(tmp_path):
    store = History(tmp_path / "history.db", 3)
    for number in range(6):
        store.append({"number": number})
    first = store.recent(limit=2)
    assert [item["number"] for item in first] == [5, 4]
    assert store.recent(before=first[-1]["id"])[0]["number"] == 3
    assert len(History(store.path, 3).recent()) == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"threshold": float("nan")},
        {"enabled": "false"},
        {"history_limit": 0},
        {"text_limit": 50},
        {"timeout_seconds": "8"},
        {"model": "   "},
    ],
)
def test_config_validation(overrides):
    with pytest.raises(ValueError):
        Settings.load(overrides)


@pytest.mark.parametrize(
    "base",
    [
        "http://api.typesafe.ai",
        "https://127.0.0.1",
        "https://192.168.1.10",
        "https://[::1]",
        "https://localhost",
        "https://jev.internal",
        "https://user:pass@api.typesafe.ai",
        "https://api.typesafe.ai?key=leak",
        "api.typesafe.ai",
        "",
    ],
)
def test_api_base_rejects_unsafe_endpoints(base):
    with pytest.raises(ValueError):
        Settings.load({"api_base": base})


@pytest.mark.parametrize(
    "base,cleaned",
    [
        ("https://api.typesafe.ai/", "https://api.typesafe.ai"),
        ("  https://openrouter.ai/api  ", "https://openrouter.ai/api"),
        (
            "https://ai-gateway.vercel.sh/typesafe",
            "https://ai-gateway.vercel.sh/typesafe",
        ),
    ],
)
def test_api_base_accepts_official_and_gateways(base, cleaned):
    assert Settings.load({"api_base": base}).api_base == cleaned


def test_config_schema_matches_defaults():
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    assert {key: value["default"] for key, value in schema.items()} == asdict(
        Settings()
    )


async def test_storage_failure_does_not_break_gate(setup_engine):
    engine, judge, store = setup_engine()

    def broken(record):
        raise OSError("simulated disk failure")

    store.append = broken
    judge.probability = 0.1
    assert not await engine.decide("pre", Message("g", "1", "u", "x"))
    assert engine.history_error


async def test_context_total_budget(setup_engine):
    engine, judge, _ = setup_engine()
    lines = [f"[{n}] " + "x" * 3995 for n in range(20)]
    msg = Message("g", "target", "u", "x", conversation=lines)
    await engine.decide("pre", msg)
    context = judge.calls[0][0]["conversation"]
    assert context == lines[-3:]
    assert sum(len(line) for line in context) == 12000
