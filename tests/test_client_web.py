import asyncio
import json
from dataclasses import asdict

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_core import Judge

from jev.client import JevClient, JevError
from jev.config import Settings
from jev.engine import Engine, Message
from jev.history import History
from jev.policy import Policy
from jev.web import create_app


class Response:
    def __init__(self, body, status=200):
        self.raw = json.dumps(body).encode()
        self.status = status
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for pos in range(0, len(self.raw), 3):
            yield self.raw[pos : pos + 3]


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


async def test_official_protocol_and_chunked_response():
    session = Session(
        Response(
            {"model": "jev-test", "answers": {"allow": {"type": "noul", "noul": 0.9}}}
        )
    )
    client = JevClient(Settings(api_key="test-secret"), session)
    assert (await client.evaluate({"text": "x"}, "pre", "规则"))["probability"] == 0.9
    url, options = session.calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert options["allow_redirects"] is False
    assert options["json"]["questions"]["allow"]["type"] == "noul"
    assert options["json"]["questions"]["allow"]["instructions"].startswith("规则")


@pytest.mark.parametrize("probability", [True, "0.9", None, -1, 2, float("nan")])
async def test_malformed_answers_are_rejected(probability):
    session = Session(
        Response(
            {
                "model": "jev",
                "answers": {"allow": {"type": "noul", "noul": probability}},
            }
        )
    )
    with pytest.raises(JevError, match="invalid_response"):
        await JevClient(Settings(api_key="secret"), session).evaluate({}, "pre", "x")


@pytest.mark.parametrize("status", [302, 401, 429, 500, 529])
async def test_http_errors_never_include_response_body(status):
    session = Session(Response({"secret": "do-not-leak"}, status))
    with pytest.raises(JevError, match=f"^http_{status}$"):
        await JevClient(Settings(api_key="secret"), session).evaluate({}, "pre", "x")
    assert len(session.calls) == 1


async def test_timeout_includes_queue():
    session = Session(Response({}))
    client = JevClient(Settings(api_key="x", timeout_seconds=0.01), session)
    client.slots = asyncio.Semaphore(0)
    with pytest.raises(JevError, match="timeout"):
        await client.evaluate({}, "pre", "x")
    assert not session.calls


async def test_missing_key_never_calls_http():
    session = Session(Response({}))
    with pytest.raises(JevError, match="missing_api_key"):
        await JevClient(Settings(), session).evaluate({}, "pre", "x")
    assert not session.calls


async def test_oversized_response_is_bounded():
    session = Session(Response({"data": "x" * 65537}))
    with pytest.raises(JevError, match="response_too_large"):
        await JevClient(Settings(api_key="x"), session).evaluate({}, "pre", "x")


async def test_network_error_is_sanitized():
    class BrokenSession:
        def post(self, *args, **kwargs):
            raise aiohttp.ClientError("private body and token")

    with pytest.raises(JevError, match="^network_error$"):
        await JevClient(Settings(api_key="x"), BrokenSession()).evaluate({}, "pre", "x")


@pytest.fixture
async def web_client(tmp_path):
    cfg = Settings(enabled=True, webui_token="test-token-long-enough")
    engine = Engine(cfg, Judge(), History(tmp_path / "history.db", 20))
    app = create_app(engine, tmp_path / "policy.json")
    async with TestClient(TestServer(app)) as client:
        yield client, engine, {"Authorization": f"Bearer {cfg.webui_token}"}


async def test_web_auth_and_status(web_client):
    client, _, headers = web_client
    for method, path in [
        ("GET", "/api/status"),
        ("GET", "/api/history"),
        ("GET", "/api/policy"),
        ("POST", "/api/policy"),
        ("POST", "/api/preview"),
        ("POST", "/api/probe"),
    ]:
        response = await client.request(method, path)
        assert response.status == 401
    response = await client.get("/api/status", headers=headers)
    data = await response.json()
    assert data["switches"]["private_enabled"] is False
    assert "token" not in json.dumps(data)
    assert "api_key" not in json.dumps(data)
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


async def test_policy_save_preview_and_csrf(web_client):
    client, engine, headers = web_client
    updated = asdict(Policy("前 {{persona}}", "后 {{bot_description}}", True))
    response = await client.post(
        "/api/policy",
        json=updated,
        headers={**headers, "Origin": "https://evil.invalid"},
    )
    assert response.status == 403
    response = await client.post("/api/policy", json=updated, headers=headers)
    assert response.status == 200 and engine.policy.include_persona
    engine.observe(
        Message("g", "1", "u", "x", persona="会话人格", persona_status="resolved")
    )
    engine.observe(Message("g", "bot:1", "bot", "hello", role="assistant"))
    response = await client.post(
        "/api/preview", json={"policy": updated, "session": "g"}, headers=headers
    )
    assert (await response.json())["pre"] == "前 会话人格"
    assert engine.decisions == 0
    invalid = {**updated, "pre_prompt": "{{invalid}}"}
    assert (
        await client.post("/api/policy", json=invalid, headers=headers)
    ).status == 400
    assert engine.policy.pre_prompt == "前 {{persona}}"


async def test_probe_and_history(web_client):
    client, engine, headers = web_client
    response = await client.post("/api/probe", json={}, headers=headers)
    assert response.status == 200
    rows = await (await client.get("/api/history", headers=headers)).json()
    assert rows["records"][0]["stage"] == "probe"
    assert rows["records"][0]["state"]["target"]["sender"] == "synthetic-user"
    assert not engine.contexts
    assert (await client.get("/api/history?before=bad", headers=headers)).status == 400


async def test_assets_and_probe_concurrency(web_client):
    client, engine, headers = web_client
    response = await client.get("/")
    assert response.status == 200 and "判断规则" in await response.text()
    assert (await client.get("/assets/no-file")).status == 404
    engine.judge.wait = asyncio.Event()
    pending = asyncio.create_task(client.post("/api/probe", json={}, headers=headers))
    for _ in range(100):
        if engine.judge.calls:
            break
        await asyncio.sleep(0.001)
    assert (await client.post("/api/probe", json={}, headers=headers)).status == 429
    engine.judge.wait.set()
    assert (await pending).status == 200
