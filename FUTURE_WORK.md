# Future Work
## 1. Close the learning loop

Today `make refresh` is manual: a human notices a good answer and promotes
it. Build the agent that promotes itself:

- Log every trace (question, modeled SQL, physical SQL, execution outcome).
- When an answer verifies (dry-run clean, rows returned, user confirms),
  queue the NL→SQL pair as a candidate for `wren/knowledge/sql/`.
- Review candidates (human or judge), merge winners, `make refresh`.

The eval harness already records modeled SQL and outcomes — one persistence
step from a system that gets smarter every week. This is upstream's
Appendix A, which they never implemented.

## 2. Multi-turn conversation

Every question spawns a fresh agent. Real analytics is iterative ("now break
that down by category", "just completed orders"). Thread conversation
history through `ask.py`, with the semantic layer keeping each turn
governed. The memory machinery (dedupe invariant, verified writes) is
exactly what this needs — extend it from preferences to dialogue state.

## 3. A UI people can touch

CLI demos don't spread. Build a minimal chat page (Streamlit or FastAPI +
HTMX) showing the answer *plus the trace*: tool used, SQL executed, budget
spend. `answer_text` / `budget_line` already separate display from trace —
the API shape is half-designed.

## 4. Harder evals

A green suite teaches nothing. Next cases:

- Multi-hop joins ("top customers by net revenue" across orders, payments,
  returns).
- Time-windowed cubes: fixed by src/agent/cube_query.py (typed literals);
  pinned by energy_cube_time_window. Retire the shim when Wren types them.
- Contradictory memories (user redefines twice — which value wins?).
- Cost assertions (scan caps, turn budgets as graded constraints).

A suite that fails is a suite that works.

## 5. Production hardening, in order

- Read-only executor split: WrenAI plans via `sql_only`, a separate
  executor runs. The MCP server already runs without `--allow-write`,
  so the philosophy is in place.
- Per-question cost caps enforced in Trino (resource groups, scan limits).
- Tenant scoping (identity propagation, row filters, memory isolation).

Upstream's
[PRODUCTION_CONSIDERATIONS.md](https://github.com/jaehyeon-kim/agentic-analytics-system/blob/main/PRODUCTION_CONSIDERATIONS.md)
is the checklist.
