"""Ask the analytics agent a question.

Usage: uv run python scripts/ask.py [--use-memory] [--domain energy|ecommerce|all] "your question"
Fresh agent per invocation: no cross-question memory unless --use-memory
enables the Mem0-backed preference tools. Configuration loaded from .env.
The answer goes to stdout; the agent's trace and `[budget]` line to stderr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.orchestrator import DOMAINS, answer_question


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the analytics agent a question.")
    parser.add_argument("question", nargs="+", help="the question, in plain English")
    parser.add_argument("--use-memory", action="store_true", help="enable saved preferences")
    parser.add_argument("--domain", choices=[*DOMAINS, "all"], default="all")
    args = parser.parse_args()

    domains = DOMAINS if args.domain == "all" else (args.domain,)
    try:
        result = answer_question(
            " ".join(args.question), user_id="cli", use_memory=args.use_memory, domains=domains
        )
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    print(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
