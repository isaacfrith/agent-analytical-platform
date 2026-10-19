# Energy demo (~10 min)

Conversational analytics over live NEM data (Open Electricity API, CC BY-NC 4.0).
The agent reasons over governed cubes and rules; WrenAI compiles the SQL.

Every answer prints on stdout, the agent's trace on stderr. Keep the trace
visible: the `Tool #N:` lines are what show the governance working.

## Before the demo

The stack (`make up`) and infra must already be running. Then:

```bash
make preflight      # AWS identity, bucket, Bedrock access
make energy-data    # refresh the 90-day window (data ends yesterday)
make energy-test    # 15 data checks against Trino
make mdl-test       # semantic layer health, no LLM
```

Skipping `make energy-data` works, but "last N days" questions then miss the
days since the last fetch (`energy-test` prints the newest day).

`make ask` loads both the ecommerce and energy prompts. For an energy-only
demo, call the script directly:

```bash
alias ask='uv run python scripts/ask.py --domain energy'
```

## Script

### 1. Governed metric: the cube does the work

```bash
ask "What share of NSW generation was renewable over the last 30 days?"
```

Look for: `query_cube` on `renewable_share`. "Renewable" comes from the
governed `is_renewable` flag (wind/solar/hydro/bioenergy, batteries excluded)
rather than the LLM's own guess.

### 2. Units the model mustn't get wrong

```bash
ask "What was the average spot price by region over the last 30 days?"
```

Look for: the `daily_price` cube with `avg_price`. Prices are rates
(AUD/MWh), so they must be averaged, never summed.

### 3. Two-step reasoning with a join

```bash
ask "On the highest-price day in the last 30 days, which fuel types generated the most?"
```

Look for: a single `run_sql` that finds the price-spike day, then joins
generation on region and day.

### 4. Capacity vs. output

```bash
ask "Show me operating coal capacity by state."
```

Look for: registered **MW** from `facilities`, not MWh from
`generation_daily`.

### 5. Refusal

```bash
ask "Show me generation by unit efficiency."
```

Look for: no `run_sql` in the trace. Nothing in the data measures
efficiency, so the agent should say so rather than make up a column.

### 6. Memory across separate agents (optional)

```bash
make memory-wipe
uv run python scripts/ask.py --domain energy --use-memory "Remember: when I say 'the big two' I mean NSW1 and VIC1."
uv run python scripts/ask.py --domain energy --use-memory "Compare emissions for the big two over the last 30 days."
make memory-check
```

Look for: the second, fresh agent resolves "the big two" from Valkey memory.

## Closing with evidence (optional)

```bash
uv run python evaluations/run_evals.py --tests energy_renewable_share,energy_price_spike_join,energy_capacity_status,energy_refuse_efficiency
```

## Notes

- Steps 1, 3, 4 and 5 are the questions the eval suite checks, so they're
  the safest. Steps 2 and 6 aren't covered by any eval, so run them once
  before the audience arrives.
- If `--use-memory` fails with a Hugging Face download error, the OS has
  purged the FastEmbed model from `$TMPDIR`. Re-fetching it takes about
  3 minutes, so do it before the demo.
- Afterwards: `make down` stops the stack. Don't run `make infra-destroy`
  unless you want the bucket deleted.
