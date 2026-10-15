"""One-step semantic learning loop: append a rule, rebuild, reindex.

Upstream equivalent of `manage_semantics.py refresh`: a confirmed
NL->SQL lesson goes from chat transcript to indexed business rule in one
command instead of edit-file + make mdl + make memory.

Usage:
    uv run python scripts/refresh_semantics.py --rule "When the user asks ..." [--title "Refunds by Status"]
    (or `make refresh rule="..." [title="..."]`)

Appends a `## <title> (<date>)` section to
wren/knowledge/rules/business_definitions.md (refuses if the identical
rule text is already present), then runs `wren context validate`,
`wren context build`, and `wren memory index`. Verify with
`make ask q="..."` on the question that motivated the rule.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repo_config import WREN_DIR, run_wren

RULES_FILE = WREN_DIR / "knowledge" / "rules" / "business_definitions.md"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Append a rule, rebuild, reindex.")
    parser.add_argument("--rule", required=True, help="rule text to append")
    parser.add_argument(
        "--title", default="", help="section title (default: first words of the rule)"
    )
    args = parser.parse_args(argv[1:])

    rule = args.rule.strip()
    if not rule:
        print("refusing to append an empty rule", file=sys.stderr)
        return 2
    title = args.title.strip() or " ".join(rule.split()[:8])
    today = datetime.date.today().isoformat()

    existing = RULES_FILE.read_text()
    if rule in existing:
        print(f"rule already present in {RULES_FILE.name} — nothing appended.")
        return 0

    section = f"\n## {title} ({today})\n- {rule}\n"
    RULES_FILE.write_text(existing.rstrip("\n") + "\n" + section)
    print(f"appended to {RULES_FILE.name}: ## {title} ({today})", flush=True)

    for step in (["context", "validate"], ["context", "build"], ["memory", "index"]):
        print(f"  [run] wren {' '.join(step)}", flush=True)
        ok, detail = run_wren(step)
        if not ok:
            print(f"wren {' '.join(step)} failed:\n{detail}", file=sys.stderr)
            print(
                "rule is saved but the layer did NOT rebuild — fix and rerun `make mdl`.",
                file=sys.stderr,
            )
            return 1
        print(f"  [ok] wren {' '.join(step)}", flush=True)

    print('refresh complete — verify with `make ask q="..."`.')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
