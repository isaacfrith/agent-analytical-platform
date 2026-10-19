"""Live questions for the energy demo UI: one question in, trace events out (SSE).

Anyone can ask, within limits. Each question passes these checks, in order,
and only counts once it actually runs:

  1. the kill switch is on (env, or an SSM parameter on the hosted server);
  2. the question is 1-500 characters;
  3. the Cloudflare Turnstile bot-check token verifies (skipped in local test mode);
  4. no other question is running (one at a time);
  5. this visitor has questions left today (2 by default);
  6. there are questions left today in total (20 by default).

Visitors are counted by IP: Cloudflare's CF-Connecting-IP when the request
came through the tunnel (the server only listens on localhost, so the tunnel
is the only way in), stored as a salted hash in Valkey for two days.

The agent is the energy-only, memory-off agent with the trace recorder on:
the same events the replay files hold, so the page renders both the same way.

Endpoints:
  GET  /api/health  status, limits left for this visitor and in total, bot-check site key
  POST /api/ask     {question, turnstile_token} -> text/event-stream of `data: <event json>`
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from config import Config

MAX_QUESTION_CHARS = 500
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
SWITCH_CACHE_S = 30
_KEY_TTL_S = int(timedelta(days=2).total_seconds())

# (question, on_event) -> None; raises on failure. Swapped out in tests.
QuestionRunner = Callable[[str, Callable[[dict], None]], Any]
# (token, visitor ip) -> True if Cloudflare says the visitor is human.
BotCheck = Callable[[str, str], Awaitable[bool]]


class Counter(Protocol):
    def incr(self, key: str) -> int: ...
    def get(self, key: str) -> Any: ...
    def expire(self, key: str, seconds: int) -> Any: ...


class UsageLimits:
    """Questions per UTC day: per visitor and in total, counted in Valkey."""

    def __init__(
        self,
        store: Counter,
        daily_cap: int,
        per_visitor_cap: int,
        salt: str = "",
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.store = store
        self.daily_cap = daily_cap
        self.per_visitor_cap = per_visitor_cap
        self.salt = salt
        self.now = now

    def _day(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def _visitor_key(self, ip: str) -> str:
        digest = hashlib.sha256(f"{self.salt}:{ip}".encode()).hexdigest()[:20]
        return f"demo:visitor:{digest}:{self._day()}"

    def _total_key(self) -> str:
        return f"demo:asks:{self._day()}"

    def _used(self, key: str) -> int:
        return int(self.store.get(key) or 0)

    def remaining_total(self) -> int:
        return max(0, self.daily_cap - self._used(self._total_key()))

    def remaining_for(self, ip: str) -> int:
        own = max(0, self.per_visitor_cap - self._used(self._visitor_key(ip)))
        return min(own, self.remaining_total())

    def take(self, ip: str) -> str | None:
        """Count one question for this visitor; or say why not ("visitor_limit"/"daily_limit")."""
        if self._used(self._visitor_key(ip)) >= self.per_visitor_cap:
            return "visitor_limit"
        if self._used(self._total_key()) >= self.daily_cap:
            return "daily_limit"
        for key in (self._visitor_key(ip), self._total_key()):
            if self.store.incr(key) == 1:
                self.store.expire(key, _KEY_TTL_S)
        return None


def turnstile_check(secret: str, transport: httpx.AsyncBaseTransport | None = None) -> BotCheck:
    """Verify a Turnstile token with Cloudflare (server-side; the secret never leaves here).

    `transport` exists for tests; production talks to Cloudflare directly.
    """

    async def verify(token: str, ip: str) -> bool:
        if not token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                resp = await client.post(
                    TURNSTILE_VERIFY_URL,
                    data={"secret": secret, "response": token, "remoteip": ip},
                )
            return bool(resp.json().get("success"))
        except (httpx.HTTPError, ValueError):
            return False  # fail closed: no verification, no question

    return verify


def live_switch(config: Config) -> Callable[[], bool]:
    """The kill switch: the SSM parameter when configured (cached briefly), else the env setting."""
    if not config.demo.switch_param:
        return lambda: config.demo.enabled
    import boto3

    ssm = boto3.client("ssm", region_name=config.aws.region)
    cache = {"at": 0.0, "on": False}

    def enabled() -> bool:
        if time.monotonic() - cache["at"] > SWITCH_CACHE_S:
            try:
                value = ssm.get_parameter(Name=config.demo.switch_param)["Parameter"]["Value"]
                cache["on"] = value.strip().lower() == "on"
            except Exception:  # noqa: BLE001 — can't read the switch: fail closed
                cache["on"] = False
            cache["at"] = time.monotonic()
        return cache["on"]

    return enabled


def visitor_ip(request: Request) -> str:
    """The visitor's IP. Trusted because the server only listens on localhost behind the tunnel."""
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )


def energy_runner(config: Config) -> QuestionRunner:
    from agent.orchestrator import answer_question

    def run(question: str, on_event: Callable[[dict], None]) -> None:
        answer_question(
            question,
            user_id="demo-live",
            config=config,
            use_memory=False,
            domains=("energy",),
            on_event=on_event,
        )

    return run


def _refuse(code: str, detail: str, status: int) -> JSONResponse:
    return JSONResponse({"code": code, "detail": detail}, status_code=status)


def create_app(
    config: Config,
    limits: UsageLimits,
    run_question: QuestionRunner,
    bot_check: BotCheck | None,
    is_enabled: Callable[[], bool],
    on_question: Callable[[], None] | None = None,
) -> Starlette:
    """bot_check=None is local test mode only; production passes turnstile_check(secret).

    on_question runs when a question starts (the hosted server's idle watchdog uses it).
    """
    busy = threading.Lock()

    async def health(request: Request) -> JSONResponse:
        ip = visitor_ip(request)
        return JSONResponse(
            {
                "ok": True,
                "enabled": is_enabled(),
                "busy": busy.locked(),
                "daily_cap": limits.daily_cap,
                "remaining": limits.remaining_total(),
                "per_visitor_cap": limits.per_visitor_cap,
                "visitor_remaining": limits.remaining_for(ip),
                "bot_check": bot_check is not None,
                "turnstile_site_key": config.demo.turnstile_site_key if bot_check else None,
            }
        )

    async def ask(request: Request) -> JSONResponse | StreamingResponse:
        if not is_enabled():
            return _refuse("off", "Live questions are switched off right now.", 503)
        try:
            body = await request.json()
        except ValueError:
            return _refuse("bad_request", "Send JSON: {question, turnstile_token}.", 400)
        question = str(body.get("question") or "").strip()
        if not question:
            return _refuse("bad_request", "Ask a question.", 400)
        if len(question) > MAX_QUESTION_CHARS:
            return _refuse(
                "bad_request", f"Keep questions under {MAX_QUESTION_CHARS} characters.", 400
            )
        ip = visitor_ip(request)
        if bot_check is not None and not await bot_check(
            str(body.get("turnstile_token") or ""), ip
        ):
            return _refuse(
                "bot_check", "The bot check didn't pass. Reload the page and try again.", 403
            )
        if not busy.acquire(blocking=False):
            return _refuse(
                "busy", "Someone else's question is running. Try again in a minute.", 429
            )
        refused = limits.take(ip)
        if refused:
            busy.release()
            if refused == "visitor_limit":
                return _refuse(
                    refused,
                    f"You've used today's {limits.per_visitor_cap} live questions. Come back "
                    "tomorrow, or pick a suggestion: they're free.",
                    429,
                )
            return _refuse(
                refused,
                "Today's live questions are all used up. Come back tomorrow, or pick a suggestion.",
                429,
            )

        if on_question is not None:
            on_question()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def push(event: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def work() -> None:
            try:
                run_question(question, push)
            except Exception as e:  # noqa: BLE001 — any failure must reach the browser as an event
                # The recorder already emitted an error event when it got that far;
                # this covers failures before the agent started (e.g. MCP spawn).
                push({"type": "error", "t": 0, "message": str(e)[:500]})
            finally:
                busy.release()  # before the end marker, so the next ask never sees a stale lock
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=work, name="demo-live-run", daemon=True).start()

        async def stream():
            errored = False
            while (event := await queue.get()) is not None:
                if event["type"] == "error":
                    if errored:
                        continue  # one error per run is enough
                    errored = True
                yield f"data: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return Starlette(
        routes=[Route("/api/health", health), Route("/api/ask", ask, methods=["POST"])],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=list(config.demo.allowed_origins),
                allow_methods=["GET", "POST"],
                allow_headers=["Content-Type"],
            )
        ],
    )


def _limits(config: Config) -> UsageLimits:
    from valkey import Valkey

    return UsageLimits(
        Valkey.from_url(config.memory.valkey_url),
        config.demo.daily_cap,
        config.demo.per_visitor_cap,
        salt=config.demo.turnstile_secret or "local",
    )


def build_app() -> Starlette:
    """Production: `uvicorn api.server:build_app --factory`. Requires the Turnstile keys."""
    config = Config.from_env()
    if not (config.demo.turnstile_secret and config.demo.turnstile_site_key):
        raise ValueError(
            "DEMO_TURNSTILE_SITE_KEY and DEMO_TURNSTILE_SECRET are required; "
            "use build_local_app for laptop testing."
        )
    return create_app(
        config,
        _limits(config),
        energy_runner(config),
        turnstile_check(config.demo.turnstile_secret),
        live_switch(config),
        on_question=_activity_marker(),
    )


def _activity_marker() -> Callable[[], None] | None:
    """Touch DEMO_ACTIVITY_FILE on each question, so the idle watchdog knows the server is in use."""
    path = os.environ.get("DEMO_ACTIVITY_FILE")
    if not path:
        return None
    return lambda: Path(path).touch()


def build_local_app() -> Starlette:
    """Laptop testing (demo_live.py --local): no bot check, same limits."""
    config = Config.from_env()
    return create_app(config, _limits(config), energy_runner(config), None, live_switch(config))
