# The semantic layer: WrenAI

Point an AI at raw tables and it will happily invent its own definition of "renewable", sum a price, or mix two markets together. The answer looks right and isn't. This project puts **WrenAI** between the agent and the data, so the definitions are written down once and every answer has to go through them.

![Query flow: how a question becomes governed, executed SQL](query_flow_diagram.png)

**1. The definitions live in [`wren/`](../wren), as WrenAI's MDL** (modeling definition language): plain YAML and Markdown, reviewed like code.

| What | Where | Example |
| --- | --- | --- |
| **Models**: the tables the agent may use, with a description of every column | [`wren/models/`](../wren/models) | `market_daily`: one row per region and day; price is a rate, so average it, never sum it |
| **Relationships**: how tables join | [`wren/relationships.yml`](../wren/relationships.yml) | `generation_daily` → `fueltech_groups` on `fueltech` |
| **Views**: governed pre-joins | [`wren/views/`](../wren/views) | `generation_with_group`: generation with its fuel group and renewable flag |
| **Cubes**: governed metrics | [`wren/cubes/`](../wren/cubes) | `renewable_share`, `daily_price`, `daily_generation` |
| **Rules**: business definitions the agent must follow | [`wren/knowledge/rules/`](../wren/knowledge/rules) | "Coal" means black *and* brown coal; "the NEM" never includes WA |
| **Example queries**: question → SQL pairs it retrieves from | [`wren/knowledge/sql/`](../wren/knowledge/sql) | Indexed with `make memory` (LanceDB) |

A metric is defined once, in the cube. Here's the renewable share, abridged from [`wren/cubes/renewable_share/metadata.yml`](../wren/cubes/renewable_share/metadata.yml):

```yaml
name: renewable_share
base_object: generation_with_group
measures:
  - name: renewable_share_pct
    expression: 100.0 * SUM(CASE WHEN is_renewable THEN energy_mwh ELSE 0 END) / NULLIF(SUM(energy_mwh), 0)
dimensions: [network, network_region]
time_dimensions: [day]
```

**2. The agent asks for a metric, not SQL.** For *"What share of NSW generation was renewable over the last 30 days?"* the agent's whole request is:

```json
{"cube": "renewable_share", "measures": ["renewable_share_pct"],
 "dimensions": ["network_region"], "filters": ["network_region:eq:NSW1", "day:gte:-30d"]}
```

**3. WrenAI compiles it to SQL**, with the governed definition filled in, and Trino runs it:

```sql
SELECT network_region, 100.0 * SUM(CASE WHEN is_renewable THEN energy_mwh ELSE 0 END)
       / NULLIF(SUM(energy_mwh), 0) AS renewable_share_pct
FROM generation_with_group
WHERE network_region = 'NSW1' AND day >= CAST('2026-09-19' AS TIMESTAMP)
GROUP BY 1
```

The agent never decided what "renewable" means, which fuels count, or how to turn daily rows into one share. Change the definition in the cube and every future answer changes with it.

**4. The agent reaches WrenAI through its MCP tools**, all read-only: `get_instructions` (the rules), `describe_cube`, `query_cube`, `dry_run` (checks SQL against the model before running it) and `run_sql`. It follows one of three paths, and the demo labels every answer with the path it took:

| Label | Path |
| --- | --- |
| ✓ **Governed cube** | A cube answered the question. The most trustworthy path. |
| ▲ **SQL fallback** | No cube fitted, so the agent wrote SQL, but only against the MDL models and views, checked with `dry_run` first. |
| ✕ **Refused** | The data can't answer the question (no hourly data, no Northern Territory), and the agent says so instead of guessing. |

**Teach it a new rule** in one step: this adds the rule to the semantic layer, rebuilds the MDL and reindexes it.

```bash
make refresh rule="Refunds by status always use return_status from returned_orders." title="Refunds by Status"
```

The agent's own instructions are plain text in [`prompts/`](../prompts): use cubes first, check SQL before running it, refuse rather than guess, and say which definition it used when a word is ambiguous.

## Known WrenAI workarounds

Two workarounds for WrenAI 0.15–0.16 bugs, to remove once fixed upstream:

- **Date filters on cubes.** WrenAI writes cube date filters as plain text, which Trino rejects. The agent's own `query_cube` ([`src/agent/cube_query.py`](../src/agent/cube_query.py)) types the dates before the query runs.
- **Row order.** WrenAI's `run_sql` can return the right rows in the wrong order. Charts and answers sort their rows, so the demo isn't affected.
