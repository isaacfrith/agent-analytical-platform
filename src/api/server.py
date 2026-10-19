"""Live mode for the energy demo UI: one question in, trace events out (SSE).

Runs on the laptop behind a Cloudflare quick tunnel (scripts/demo_live.py).
Guards, in order: shared passcode, one run at a time, a daily question cap
counted in Valkey across all visitors. The agent is the energy-only,
memory-off agent with the trace recorder on — the same events the replay
files hold, so the page renders both the same way.

Endpoints:
  GET  /api/health  {ok, daily_cap, remaining, busy}
  POST /api/ask     {question, passcode} -> text/event-stream of `data: <event json>`
"""

from __future__ import annotations

import asyncio
import hmac
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from config import Config

MAX_QUESTION_CHARS = 500
WRONG_PASSCODE_DELAY_S = 1.0
_KEY_PREFIX = "demo:asks:"  # one counter per UTC day

# (question, on_event) -> None; raises on failure. Swapped out in tests.
QuestionRunner = Callable[[str, Callable[[dict], None]], Any]


class Counter(Protocol):
    def incr(self, key: str) -> int: ...
    def get(self, key: str) -> Any: ...
    def expire(self, key: str, seconds: int) -> Any: ...


class DailyCap:
    """Live questions per UTC day, shared by every visitor."""

    def __init__(self, store: Counter, cap: int, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.store = store
        self.cap = cap
        self.now = now

    def _key(self) -> str:
        return _KEY_PREFIX + self.now().strftime("%Y-%m-%d")

    def used(self) -> int:
        return int(self.store.get(self._key()) or 0)

    def remaining(self) -> int:
        return max(0, self.cap - self.used())

    def take(self) -> bool:
        """Count one question; False (and no run) once the cap is reached."""
        key = self._key()
        n = self.store.incr(key)
        if n == 1:
            self.store.expire(key, int(timedelta(days=2).total_seconds()))
        return n <= self.cap


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


def create_app(config: Config, cap: DailyCap, run_question: QuestionRunner) -> Starlette:
    if not config.demo.passcode:
        raise ValueError("DEMO_PASSCODE is not set — live mode refuses to start without it.")
    busy = threading.Lock()

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "daily_cap": cap.cap,
            "remaining": cap.remaining(),
            "busy": busy.locked(),
        })

    async def ask(request: Request) -> JSONResponse | StreamingResponse:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"detail": "Send JSON: {question, passcode}."}, status_code=400)
        question = str(body.get("question") or "").strip()
        passcode = str(body.get("passcode") or "")
        if not hmac.compare_digest(passcode.encode(), config.demo.passcode.encode()):
            await asyncio.sleep(WRONG_PASSCODE_DELAY_S)
            return JSONResponse({"detail": "Wrong passcode."}, status_code=401)
        if not question:
            return JSONResponse({"detail": "Ask a question."}, status_code=400)
        if len(question) > MAX_QUESTION_CHARS:
            return JSONResponse({"detail": f"Keep questions under {MAX_QUESTION_CHARS} characters."}, status_code=400)
        if not busy.acquire(blocking=False):
            return JSONResponse({"detail": "Someone else's question is running. Try again in a minute."}, status_code=429)
        if not cap.take():
            busy.release()
            return JSONResponse({"detail": "Today's live questions are used up. Come back tomorrow, or try a demo question."}, status_code=429)

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


def build_app() -> Starlette:
    """uvicorn entry point: `uvicorn api.server:build_app --factory`."""
    from valkey import Valkey

    config = Config.from_env()
    cap = DailyCap(Valkey.from_url(config.memory.valkey_url), config.demo.daily_cap)
    return create_app(config, cap, energy_runner(config))
