"""Live questions: kill switch, bot check, visitor/daily/owner limits, and the SSE stream."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from starlette.testclient import TestClient

from api.server import UsageLimits, build_app, create_app, owner_check, turnstile_check
from config import AgentConfig, AwsConfig, Config, DemoConfig, MemoryConfig

SITE = "https://isaacfrith.github.io"
PASS = "owner-pass-0123456789"


class FakeCounter:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, seconds):
        self.expiries[key] = seconds


def make_config(**demo) -> Config:
    return Config(
        agent=AgentConfig(),
        memory=MemoryConfig(valkey_url="redis://unused", collection_name="unused"),
        aws=AwsConfig(profile="p", region="r", bedrock_model_id="m", s3_bucket="b"),
        demo=DemoConfig(turnstile_site_key="site-key", **demo),
    )


def fake_run(question, on_event):
    on_event({"type": "question", "t": 0, "question": question})
    on_event({"type": "answer", "t": 1, "text": "42", "path": "cube"})


class Harness:
    """An app with fake storage, a scriptable bot check and a switch."""

    def __init__(
        self, daily_cap=20, per_visitor_cap=2, owner_cap=30, run=fake_run, bot_ok=True, owner=PASS
    ):
        self.counter = FakeCounter()
        self.questions_started = 0
        self.bot_ok = bot_ok
        self.bot_calls: list[tuple[str, str]] = []
        self.on = True

        async def bot_check(token, ip):
            self.bot_calls.append((token, ip))
            return self.bot_ok

        limits = UsageLimits(self.counter, daily_cap, per_visitor_cap, owner_cap)

        def started():
            self.questions_started += 1

        app = create_app(
            make_config(), limits, run, bot_check, lambda: self.on, started, owner_check(owner)
        )
        self.client = TestClient(app)

    def ask(self, question="price?", ip="1.1.1.1", token="tok", owner_pass=None):
        body = {"question": question, "turnstile_token": token}
        if owner_pass is not None:
            body["owner_pass"] = owner_pass
        return self.client.post(
            "/api/ask",
            json=body,
            headers={"CF-Connecting-IP": ip},
        )

    def used(self, prefix="demo:asks:") -> int:
        return sum(v for k, v in self.counter.values.items() if k.startswith(prefix))

    def health(self, ip="1.1.1.1", owner_pass=None) -> dict:
        headers = {"CF-Connecting-IP": ip}
        if owner_pass is not None:
            headers["X-Owner-Pass"] = owner_pass
        return self.client.get("/api/health", headers=headers).json()


def events(resp) -> list[dict]:
    return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]


def test_ask_streams_events_and_counts_once():
    h = Harness()
    resp = h.ask()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert [e["type"] for e in events(resp)] == ["question", "answer"]
    assert h.used() == 1
    assert h.bot_calls == [("tok", "1.1.1.1")]


def test_health_reports_this_visitors_and_the_days_remaining():
    h = Harness()
    h.ask(ip="1.1.1.1")
    health = h.client.get("/api/health", headers={"CF-Connecting-IP": "1.1.1.1"}).json()
    assert health | {"busy": False} == {
        "ok": True,
        "enabled": True,
        "busy": False,
        "daily_cap": 20,
        "remaining": 19,
        "per_visitor_cap": 2,
        "visitor_remaining": 1,
        "bot_check": True,
        "turnstile_site_key": "site-key",
    }
    other = h.client.get("/api/health", headers={"CF-Connecting-IP": "2.2.2.2"}).json()
    assert other["visitor_remaining"] == 2


def test_two_questions_per_visitor_then_refused():
    h = Harness()
    assert h.ask(ip="1.1.1.1").status_code == 200
    assert h.ask(ip="1.1.1.1").status_code == 200
    resp = h.ask(ip="1.1.1.1")
    assert resp.status_code == 429 and resp.json()["code"] == "visitor_limit"
    assert h.ask(ip="2.2.2.2").status_code == 200  # someone else still can
    assert h.used() == 3


def test_daily_cap_across_all_visitors():
    h = Harness(daily_cap=3)
    for ip in ("1.1.1.1", "1.1.1.1", "2.2.2.2"):
        assert h.ask(ip=ip).status_code == 200
    resp = h.ask(ip="3.3.3.3")
    assert resp.status_code == 429 and resp.json()["code"] == "daily_limit"
    assert h.used() == 3


def test_failed_bot_check_is_refused_and_not_counted():
    h = Harness(bot_ok=False)
    resp = h.ask()
    assert resp.status_code == 403 and resp.json()["code"] == "bot_check"
    assert h.used() == 0


def test_kill_switch_refuses_before_anything_else():
    h = Harness()
    h.on = False
    resp = h.ask()
    assert resp.status_code == 503 and resp.json()["code"] == "off"
    assert h.bot_calls == [] and h.used() == 0


def test_question_validation_is_not_counted():
    h = Harness()
    assert h.ask(question=" ").status_code == 400
    assert h.ask(question="x" * 501).status_code == 400
    assert h.used() == 0 and h.bot_calls == []


def test_runner_failure_becomes_one_error_event():
    def boom(question, on_event):
        on_event({"type": "error", "t": 0, "message": "bedrock down"})
        raise RuntimeError("bedrock down")

    resp = Harness(run=boom).ask()
    assert [e["type"] for e in events(resp)] == ["error"]


def test_visitors_are_stored_as_hashes_that_expire():
    counter = FakeCounter()
    limits = UsageLimits(
        counter, 20, 2, salt="s", now=lambda: datetime(2026, 10, 20, 23, 59, tzinfo=UTC)
    )
    limits.take("203.0.113.9")
    keys = list(counter.values)
    assert "demo:asks:2026-10-20" in keys
    assert not any("203.0.113.9" in k for k in keys)  # the raw IP is never stored
    assert set(counter.expiries.values()) == {172800}


def test_cors_allows_the_site_and_rejects_other_origins():
    client = Harness().client
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    ok = client.options("/api/ask", headers={"Origin": SITE, **preflight})
    assert ok.status_code == 200
    bad = client.options("/api/ask", headers={"Origin": "https://evil.example", **preflight})
    assert bad.status_code == 400


def test_turnstile_check_asks_cloudflare_and_fails_closed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"success": "secret=s3" in seen["body"]})

    verify = turnstile_check("s3", transport=httpx.MockTransport(handler))
    assert asyncio.run(verify("token-1", "1.1.1.1")) is True
    assert "response=token-1" in seen["body"] and "remoteip=1.1.1.1" in seen["body"]
    assert asyncio.run(verify("", "1.1.1.1")) is False  # no token, no network call

    def down(request):
        raise httpx.ConnectError("unreachable")

    assert asyncio.run(turnstile_check("s3", httpx.MockTransport(down))("t", "ip")) is False


def test_production_app_refuses_to_start_without_turnstile_keys(monkeypatch):
    for key in ("DEMO_TURNSTILE_SITE_KEY", "DEMO_TURNSTILE_SECRET"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr("config.load_dotenv", lambda *a, **k: None)
    for key, value in (
        ("AWS_PROFILE", "p"),
        ("AWS_REGION", "r"),
        ("BEDROCK_MODEL_ID", "m"),
        ("AWS_S3_BUCKET", "b"),
    ):
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="TURNSTILE"):
        build_app()


def test_activity_is_marked_only_for_questions_that_run():
    h = Harness(per_visitor_cap=1)
    h.ask()
    h.ask()  # refused by the limit
    h.ask(question=" ")  # refused by validation
    assert h.questions_started == 1


def test_owner_pass_has_its_own_allowance_and_leaves_visitors_alone():
    h = Harness(per_visitor_cap=1, owner_cap=3)
    for _ in range(3):
        assert h.ask(owner_pass=PASS).status_code == 200  # past the per-visitor 1
    resp = h.ask(owner_pass=PASS)
    assert resp.status_code == 429 and resp.json()["code"] == "owner_limit"
    assert h.used("demo:owner:") == 3
    assert h.used() == 0  # visitors' daily pool untouched
    assert h.ask().status_code == 200  # the same browser without the pass is still a visitor


def test_owner_questions_still_need_the_bot_check_and_switch():
    h = Harness(bot_ok=False)
    resp = h.ask(owner_pass=PASS)
    assert resp.status_code == 403 and resp.json()["code"] == "bot_check"
    h.on = False
    assert h.ask(owner_pass=PASS).json()["code"] == "off"
    assert h.used("demo:owner:") == 0


def test_owner_questions_still_run_one_at_a_time():
    import threading

    release = threading.Event()

    def slow(question, on_event):
        release.wait(5)
        fake_run(question, on_event)

    h = Harness(run=slow)
    first = threading.Thread(target=h.ask)
    first.start()
    try:
        for _ in range(50):
            if h.health()["busy"]:
                break
            threading.Event().wait(0.02)
        resp = h.ask(owner_pass=PASS)
        assert resp.status_code == 429 and resp.json()["code"] == "busy"
    finally:
        release.set()
        first.join()


def test_wrong_owner_pass_is_refused_not_downgraded():
    h = Harness()
    resp = h.ask(owner_pass="wrong-pass-0123456789")
    assert resp.status_code == 403 and resp.json()["code"] == "owner_pass"
    assert h.used() == 0 and h.used("demo:owner:") == 0 and h.bot_calls == []


def test_health_reports_the_owner_allowance_only_for_the_right_pass():
    h = Harness(owner_cap=30)
    h.ask(owner_pass=PASS)
    assert "owner" not in h.health()
    assert h.health(owner_pass="nope")["owner"] is False
    good = h.health(owner_pass=PASS)
    assert (good["owner"], good["owner_cap"], good["owner_remaining"]) == (True, 30, 29)
    assert good["visitor_remaining"] == 2


def test_owner_pass_is_off_when_unset_or_too_short():
    assert owner_check(None) is None
    assert owner_check("set-me") is None
    assert owner_check("short-pass") is None
    h = Harness(owner=None)
    resp = h.ask(owner_pass=PASS)
    assert resp.status_code == 403 and resp.json()["code"] == "owner_pass"


def test_cors_allows_the_owner_pass_header_from_the_site():
    resp = Harness().client.options(
        "/api/health",
        headers={
            "Origin": SITE,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-owner-pass",
        },
    )
    assert resp.status_code == 200
