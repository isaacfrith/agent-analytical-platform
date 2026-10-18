"""Ask the analytics agent a question.

Usage: uv run python scripts/ask.py [--use-memory] [--domain energy|ecommerce|all] "your question"
Fresh agent per invocation — no cross-question memory unless --use-memory
enables the Mem0-backed preference tools.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.orchestrator import DOMAINS, answer_question


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--use-memory"]
    use_memory = len(args) != len(argv[1:])
    domains: tuple[str, ...] = DOMAINS
    if "--domain" in args:
        i = args.index("--domain")
        try:
            flag = args.pop(i + 1)
            args.pop(i)
        except IndexError:
            print("missing value for --domain (energy|ecommerce|all)")
            return 2
        if flag == "all":
            domains = DOMAINS
        elif flag in DOMAINS:
            domains = (flag,)
        else:
            print(f"unknown --domain {flag!r} (energy|ecommerce|all)")
            return 2
    if not args:
        print(
            'usage: uv run python scripts/ask.py [--use-memory] [--domain energy|ecommerce|all] "your question"'
        )
        return 2
    question = " ".join(args)
    result = answer_question(question, user_id="cli", use_memory=use_memory, domains=domains)
    print(result.metrics_line, file=sys.stderr)
    print(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
