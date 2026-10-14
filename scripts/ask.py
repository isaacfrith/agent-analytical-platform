"""Ask the Phase 4 analytics agent a question.

Usage: uv run python scripts/ask.py [--use-memory] "your question"
Fresh agent per invocation — no cross-question memory until Phase 5
(--use-memory enables the Mem0-backed preference tools). Budgets are
enforced per-invocation by the framework (turns + total tokens).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.orchestrator import (
    TOTAL_TOKENS_BUDGET,
    TURNS_BUDGET,
    answer_text,
    budget_line,
    build_agent,
    wren_mcp_tools,
)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--use-memory"]
    use_memory = len(args) != len(argv[1:])
    if not args:
        print('usage: uv run python scripts/ask.py [--use-memory] "your question"')
        return 2
    question = " ".join(args)
    with wren_mcp_tools() as tools:
        agent = build_agent(list(tools), use_memory=use_memory)
        result = None
        try:
            result = agent(
                question,
                limits={"turns": TURNS_BUDGET, "total_tokens": TOTAL_TOKENS_BUDGET},
            )
        finally:
            # Spend report prints even on failure; any traceback propagates.
            print(budget_line(result), file=sys.stderr)
    print(answer_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
