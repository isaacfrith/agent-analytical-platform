"""Phase 6 eval harness: isolated agents, deterministic asserts, judge as last resort.

Each case is a sequence of steps (single-shot cases have one). Every step
spawns a FRESH ask.py subprocess — agents never share a conversation; memory
cases share only the Valkey store, which is the point under test.

Grading order per step:
1. Deterministic asserts (tool regex on combined output, text/regex on the
   answer) — cheap, exact, no flake.
2. Bedrock-judge asserts — only where judgment is irreducible (ambiguity
   explicitness, refusal grace). The judge runs at temperature=0 with a tight
   token cap; its calls are counted and reported (judge spend is real spend).

Strict counting: ERROR and UNKNOWN verdicts fail. The first failing assert
stops the step; the first failing step stops the case (abort-on-first-failure).

Setup: `wren context build` once up front (the MCP server warns on stale MDL
but serves it — evals must grade the current project), plus a Valkey wipe
before any case flagged clean_store (entity ghosts proved shared state lies).

Usage:
    uv run python evaluations/run_evals.py [--tests ID,...] [--limit N]
                                           [--list] [--skip-setup]
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from agent.memory import MEMORY_USER_ID, as_list, get_store
from repo_config import VALKEY_INDEXES, VALKEY_URL, is_memory, run_wren

DEFAULT_SUITE = REPO_ROOT / "evaluations" / "golden_suite.json"
DEFAULT_REPORT = REPO_ROOT / "evaluations" / "last_run.md"
STEP_TIMEOUT_S = 600
JUDGE_MAX_TOKENS = 300
TRANSCRIPT_CHARS = 6000

BUDGET_RE = re.compile(r"\[budget\] turns=(\d+)/\d+\s+tokens=([\d?]+)/")
TOOL_RE_TEMPLATE = r"Tool #\d+: {tool}\b"

judge_calls = 0
judge_tokens = 0


def load_suite(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        data = json.load(f)
    return data["cases"] if isinstance(data, dict) else data


def wipe_store() -> str:
    """Delete the mem0 namespaces + indexes so the next case starts clean."""
    from valkey import Valkey
    from valkey.exceptions import ResponseError

    client = Valkey.from_url(VALKEY_URL)
    keys = list(client.scan_iter("mem0:*"))
    n = client.delete(*keys) if keys else 0
    for index in VALKEY_INDEXES:
        try:
            client.execute_command("FT.DROPINDEX", index)
        except ResponseError as e:  # index may not exist yet — not a failure
            print(f"  [setup] drop {index}: {e}", flush=True)
    return f"wiped {n} key(s), dropped indexes"


def wren_build() -> str:
    """Recompile the MDL so evals grade the current project, not a stale one."""
    ok, detail = run_wren(["context", "build"], timeout_s=STEP_TIMEOUT_S)
    if not ok:
        raise RuntimeError(f"wren context build failed:\n{detail}")
    return "wren context build OK"


def run_step(question: str, use_memory: bool) -> dict[str, Any]:
    """Spawn one fresh agent; capture answer (stdout) and transcript (stderr)."""
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "ask.py")]
    if use_memory:
        cmd.append("--use-memory")
    cmd.append(question)
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "",
            "combined": "",
            "turns": "?",
            "tokens": "?",
            "note": f"TIMEOUT after {STEP_TIMEOUT_S}s",
        }
    stderr = proc.stderr or ""
    stdout = proc.stdout or ""
    match = BUDGET_RE.search(stderr)
    turns = match.group(1) if match else "?"
    tokens = match.group(2) if match else "?"
    note = "" if proc.returncode == 0 else f"exit={proc.returncode}"
    return {
        "ok": proc.returncode == 0,
        "stdout": stdout,
        "stderr": stderr,
        "combined": stdout + "\n" + stderr,
        "turns": turns,
        "tokens": tokens,
        "note": note,
    }


def judge_call(question: str, answer: str, rubric: str) -> tuple[str, str]:
    """One Bedrock Converse grading call at temperature=0. Verdict + reason."""
    global judge_calls, judge_tokens
    import os

    from dotenv import load_dotenv

    load_dotenv()
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    judge_calls += 1
    session = boto3.Session(
        profile_name=os.environ["AWS_PROFILE"],
        region_name=os.environ["AWS_REGION"],
    )
    client = session.client("bedrock-runtime")
    prompt = (
        "You grade an analytics agent's answer against a rubric. "
        "Reply with PASS or FAIL as the very first word, then one sentence.\n\n"
        f"Question: {question}\n\nRubric: {rubric}\n\n"
        f"Answer (truncated):\n{answer[:TRANSCRIPT_CHARS]}"
    )
    try:
        resp = client.converse(
            modelId=os.environ["BEDROCK_MODEL_ID"],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0, "maxTokens": JUDGE_MAX_TOKENS},
        )
    except (BotoCoreError, ClientError) as e:  # outage fails the case, loudly
        return "ERROR", f"judge call failed: {e}"
    usage = resp.get("usage", {})
    judge_tokens += int(usage.get("totalTokens", 0))
    text = resp["output"]["message"]["content"][0]["text"].strip()
    first = text.split("\n", 1)[0].strip().upper()
    if first.startswith("PASS"):
        return "PASS", text
    if first.startswith("FAIL"):
        return "FAIL", text
    return "UNKNOWN", text


def store_facts() -> list[dict[str, Any]]:
    """Live [mem]-tagged facts from Valkey — the honest oracle for save steps.

    Answer text echoes user words whether the write landed or not; only the
    store knows. Entities are excluded (fragment mentions, not facts).
    """
    response = get_store().get_all(filters={"user_id": MEMORY_USER_ID})
    return [
        r
        for r in as_list(response)
        if isinstance(r, dict) and is_memory(r.get("id", ""))
    ]


def check_assert(
    spec: dict[str, Any], question: str, stdout: str, combined: str
) -> tuple[str, str]:
    """Return (verdict, detail) for one assert spec. Judge only on 'judge'."""
    kind = spec.get("type", "?")
    where = spec.get("where", "stdout")
    haystack = combined if where == "combined" else stdout
    if kind == "tool_used":
        pat = TOOL_RE_TEMPLATE.format(tool=re.escape(spec["tool"]))
        if re.search(pat, combined):
            return "PASS", f"tool {spec['tool']} invoked"
        return "FAIL", f"tool {spec['tool']} not found in transcript"
    if kind == "tool_absent":
        pat = TOOL_RE_TEMPLATE.format(tool=re.escape(spec["tool"]))
        if re.search(pat, combined):
            return "FAIL", f"tool {spec['tool']} was invoked (must be absent)"
        return "PASS", f"tool {spec['tool']} never invoked"
    if kind == "contains":
        if spec["text"] in haystack:
            return "PASS", f"found {spec['text']!r}"
        return "FAIL", f"missing {spec['text']!r}"
    if kind == "absent":
        if spec["text"] in haystack:
            return "FAIL", f"forbidden {spec['text']!r} present"
        return "PASS", f"{spec['text']!r} absent"
    if kind == "matches_regex":
        if re.search(spec["pattern"], haystack):
            return "PASS", f"regex /{spec['pattern']}/ matched"
        return "FAIL", f"regex /{spec['pattern']}/ not matched"
    if kind == "absent_regex":
        if re.search(spec["pattern"], haystack):
            return "FAIL", f"forbidden regex /{spec['pattern']}/ matched"
        return "PASS", f"regex /{spec['pattern']}/ absent"
    if kind == "judge":
        return judge_call(question, stdout, spec["rubric"])
    if kind == "store_contains":
        facts = store_facts()
        hits = [f for f in facts if spec["text"] in (f.get("memory", "") or "")]
        if hits:
            return "PASS", f"store holds fact containing {spec['text']!r}"
        known = [str(f.get("memory", "?"))[:80] for f in facts]
        return (
            "FAIL",
            f"store holds {len(facts)} fact(s), none with {spec['text']!r}: {known}",
        )
    if kind == "store_absent":
        facts = store_facts()
        hits = [f for f in facts if spec["text"] in (f.get("memory", "") or "")]
        if hits:
            return (
                "FAIL",
                f"rival still stored: {[str(f.get('memory', '?'))[:80] for f in hits]}",
            )
        return "PASS", f"no stored fact contains {spec['text']!r}"
    if kind == "store_matches_regex":
        facts = store_facts()
        hits = [
            f for f in facts if re.search(spec["pattern"], f.get("memory", "") or "")
        ]
        if hits:
            return "PASS", f"store holds fact matching /{spec['pattern']}/"
        known = [str(f.get("memory", "?"))[:80] for f in facts]
        return (
            "FAIL",
            f"store holds {len(facts)} fact(s), none matching /{spec['pattern']}/: {known}",
        )
    if kind == "store_absent_regex":
        facts = store_facts()
        hits = [
            f for f in facts if re.search(spec["pattern"], f.get("memory", "") or "")
        ]
        if hits:
            return (
                "FAIL",
                f"rival still stored: {[str(f.get('memory', '?'))[:80] for f in hits]}",
            )
        return "PASS", f"no stored fact matches /{spec['pattern']}/"
    return "ERROR", f"unknown assert type {kind!r}"


def grade_step(step: dict[str, Any], result: dict[str, Any]) -> tuple[str, str, str]:
    """Grade one attempt; return (verdict, detail, failed_assert_type)."""
    q = step["question"]
    for spec in step.get("asserts", []):
        verdict, detail = check_assert(spec, q, result["stdout"], result["combined"])
        mark = "ok" if verdict == "PASS" else "FAIL"
        print(f"    [{mark}] {spec.get('type')}: {detail[:160]}", flush=True)
        if verdict != "PASS":
            return verdict, detail, str(spec.get("type", "?"))
    return "PASS", "all asserts held", ""


# Asserts that depend on nondeterministic subsystems (mem0's extraction judge,
# the Bedrock grading judge) get one fresh-agent retry before failing loud.
# Deterministic transcript asserts never retry: a behaviour change must stay red.
RETRYABLE_ASSERTS = frozenset(
    {
        "judge",
        "store_contains",
        "store_absent",
        "store_matches_regex",
        "store_absent_regex",
    }
)


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one case's steps in order; abort on the first failure."""
    case_id = case.get("id", "?")
    if case.get("clean_store"):
        print(f"  [setup] {wipe_store()}", flush=True)
    step_rows: list[dict[str, Any]] = []
    for i, step in enumerate(case.get("steps", []), 1):
        q = step["question"]
        print(f"  step {i}: {q[:80]}", flush=True)
        result = run_step(q, step.get("use_memory", False))
        verdict, detail = "PASS", ""
        if not result["ok"] and not result["stdout"]:
            verdict, detail = "ERROR", result["note"] or "no output"
        else:
            for spec in step.get("asserts", []):
                verdict, detail = check_assert(
                    spec, q, result["stdout"], result["combined"]
                )
                mark = "ok" if verdict == "PASS" else "FAIL"
                print(f"    [{mark}] {spec.get('type')}: {detail[:160]}", flush=True)
                if verdict != "PASS":
                    failed_kind = str(spec.get("type", "?"))
                    if failed_kind in RETRYABLE_ASSERTS:
                        print(
                            "    [retry] flaky-oracle assert — one fresh attempt",
                            flush=True,
                        )
                        result = run_step(q, step.get("use_memory", False))
                        verdict, detail, _ = grade_step(step, result)
                        if verdict == "PASS":
                            detail += " (passed on retry)"
                    break
        row = {
            "step": i,
            "question": q,
            "verdict": verdict,
            "detail": detail,
            "turns": result["turns"],
            "tokens": result["tokens"],
            "answer": result["stdout"],
        }
        step_rows.append(row)
        if verdict != "PASS":
            break
    case_verdict = "PASS" if all(r["verdict"] == "PASS" for r in step_rows) else "FAIL"
    return {
        "id": case_id,
        "category": case.get("category", "?"),
        "verdict": case_verdict,
        "steps": step_rows,
    }


def write_report(
    path: Path,
    setup_lines: list[str],
    cases: list[dict[str, Any]],
    excerpts: list[dict[str, str]],
) -> None:
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    passed = sum(1 for c in cases if c["verdict"] == "PASS")
    lines = [
        f"# Eval run — {now}",
        "",
        f"{passed}/{len(cases)} cases passed.",
        "",
        "## Setup",
        "",
        *[f"- {s}" for s in setup_lines],
        "",
        "## Results",
        "",
        "| case | step | verdict | turns | tokens | detail |",
        "| ---- | ---- | ------- | ----- | ------ | ------ |",
    ]
    for c in cases:
        for r in c["steps"]:
            q = r["question"][:60].replace("|", "/")
            d = r["detail"][:120].replace("|", "/").replace("\n", " ")
            lines.append(
                f"| {c['id']} | {r['step']} ({q}) | {r['verdict']} "
                f"| {r['turns']} | {r['tokens']} | {d} |"
            )
    lines += [
        "",
        "## Judge spend",
        "",
        f"- judge calls: {judge_calls}, judge tokens: {judge_tokens}",
        "",
    ]
    if excerpts:
        lines += ["## Failure excerpts", ""]
        for e in excerpts:
            lines += [
                f"### {e['case']} step {e['step']} [{e['verdict']}]",
                "",
                f"Q: {e['question']}",
                "",
                f"assert: {e['detail']}",
                "",
                "```",
                e["answer"][:1500],
                "```",
                "",
            ]
    path.write_text("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Phase 6 eval harness")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--tests", default="", help="comma-separated case IDs")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--skip-setup", action="store_true")
    args = parser.parse_args(argv[1:])

    cases = load_suite(Path(args.suite))
    if args.list:
        for c in cases:
            print(
                f"{c.get('id')} [{c.get('category')}] ({len(c.get('steps', []))} steps)"
            )
        return 0
    if args.tests:
        wanted = {t.strip() for t in args.tests.split(",")}
        cases = [c for c in cases if c.get("id") in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no cases selected")
        return 2

    print(f"Running {len(cases)} case(s), strictly sequential.", flush=True)
    setup_lines: list[str] = []
    if args.skip_setup:
        setup_lines.append("wren build skipped (--skip-setup)")
    else:
        try:
            setup_lines.append(wren_build())
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            setup_lines.append("wren build FAILED — graded stale MDL")
    if any(c.get("clean_store") for c in cases):
        setup_lines.append(wipe_store() + " (pre-run)")

    results = [run_case(c) for c in cases]
    excerpts = [
        {
            "case": c["id"],
            "step": str(r["step"]),
            "verdict": r["verdict"],
            "question": r["question"],
            "detail": r["detail"],
            "answer": r.get("answer", ""),
        }
        for c in results
        for r in c["steps"]
        if r["verdict"] != "PASS"
    ]
    write_report(Path(args.report), setup_lines, results, excerpts)

    print("\n==== summary ====")
    for c in results:
        print(f"[{c['verdict']}] {c['id']}")
    passed = sum(1 for c in results if c["verdict"] == "PASS")
    print(f"{passed}/{len(results)} passed — report: {args.report}")
    print(f"judge calls: {judge_calls}, judge tokens: {judge_tokens}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
