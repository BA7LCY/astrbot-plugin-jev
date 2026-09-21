import asyncio
import json
from dataclasses import asdict, replace

import pytest

from jev.client import JevError
from jev.config import Settings
from jev.engine import Engine, Message
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
        cfg = Settings.load({"enabled": True, **overrides})
        judge = Judge()
        store = History(tmp_path / "history.db", cfg.history_limit)
        return Engine(cfg, judge, store), judge, store

    return build


@pytest.mark.parametrize(
    "probability,allowed", [(0.64, False), (0.65, True), (1, True)]
)
async def test_threshold_and_history(setup_engine, probability, allowed):
    engine, judge, store = setup_engine()
    judge.probability = probability
    msg = Message("group-a", "1", "user", "hello")
    engine.observe(msg)
    assert await engine.decide("pre", msg) is allowed
    record = store.recent()[0]
    assert record["allowed"] is allowed
    assert record["state"]["target"]["text"] == "hello"
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


def test_context_bounds_and_inflight_recall(setup_engine):
    engine, _, _ = setup_engine(max_sessions=1, context_messages=1, text_limit=100)
    msg = Message("g", "1", "u", "x" * 200)
    engine.observe(msg)
    engine.observe(Message("other", "2", "u", "new"))
    assert list(engine.contexts) == ["other"]
    assert len(msg.text) == 100
    engine.recall("g", "1")
    assert msg.recalled
    engine.recall("early", "3")
    later = Message("early", "3", "u", "x")
    engine.observe(later)
    assert later.recalled


async def test_context_and_history_disabled(setup_engine):
    engine, judge, store = setup_engine(context_enabled=False, history_enabled=False)
    msg = Message("g", "1", "u", "x")
    engine.observe(msg)
    await engine.decide("pre", msg)
    assert not engine.contexts and not store.recent()
    assert judge.calls[0][0]["conversation"] == []


async def test_latest_context_is_used_for_post(setup_engine):
    engine, judge, _ = setup_engine()
    msg = Message("g", "1", "u", "question")
    engine.observe(msg)
    await engine.decide("pre", msg)
    engine.observe(Message("g", "2", "u", "不要回答了"))
    await engine.decide("post", msg, "answer")
    assert judge.calls[-1][0]["conversation"][-1]["text"] == "不要回答了"


async def test_persona_template_and_privacy(setup_engine):
    engine, judge, store = setup_engine()
    msg = Message("g", "1", "u", "x", persona="秘密人格", persona_id="custom")
    engine.observe(msg)
    await engine.decide("pre", msg)
    assert "秘密人格" not in json.dumps(store.recent(), ensure_ascii=False)
    engine.policy = Policy("规则：{{persona}} / {{bot_description}}", "发送？", True)
    await engine.decide("pre", msg)
    assert "秘密人格" in judge.calls[-1][2]
    assert store.recent()[0]["include_persona"] is True
    msg.persona_status = "unavailable"
    assert not await engine.decide("post", msg)
    assert store.recent()[0]["error"] == "persona_unavailable"


def test_policy_validation_and_atomic_persistence(tmp_path):
    p = Policy("{{persona}} / {{bot_description}}", "发送？", True)
    assert (
        p.render("pre", "设定", "{{bot_description}}") == "{{bot_description}} / 设定"
    )
    path = tmp_path / "policy.json"
    p.save(path)
    Policy().save(path)
    assert Policy.parse(json.loads(path.read_text(encoding="utf-8"))) == Policy()
    assert json.loads(
        path.with_suffix(".json.bak").read_text(encoding="utf-8")
    ) == asdict(p)
    for data in [
        {**asdict(p), "pre_prompt": "{{unknown}}"},
        {**asdict(p), "pre_prompt": "{{persona}}{{persona}}"},
        {**asdict(p), "include_persona": "false"},
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
        {"context_messages": 0},
        {"text_limit": 50},
        {"timeout_seconds": "8"},
        {"model": "   "},
    ],
)
def test_config_validation(overrides):
    with pytest.raises(ValueError):
        Settings.load(overrides)


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
    engine, judge, _ = setup_engine(text_limit=4000, context_messages=50)
    for number in range(20):
        engine.observe(Message("g", str(number), "u", "x" * 4000))
    await engine.decide("pre", Message("g", "target", "u", "x"))
    context = judge.calls[0][0]["conversation"]
    assert sum(len(item["text"]) for item in context) <= 12000
    assert context[-1]["message_id"] == "19"
