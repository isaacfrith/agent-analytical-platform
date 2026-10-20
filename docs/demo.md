# Using and updating the demo

Open the [live demo](https://isaacfrith.github.io/agent-analytical-platform/). It has two tabs.

## The "Ask" tab

1. **Click a suggested question** in the middle panel. Three suggestions rotate there, drawn from 24 recorded questions (those tagged *Beyond the data* show the agent refusing). Each replays a real recorded run of the agent: free, instant and identical every time. After an answer, **Ask next** offers related questions.
2. **Read the three panels from left to right:**

   | Panel | What it shows |
   | --- | --- |
   | **How it got there** (left) | Each step the agent took, with how long it took and its path label. Expand a step to see the request the agent sent and the SQL WrenAI compiled. The meters show how much of the budget (10 turns, 150k tokens) it used. |
   | **Answer** (middle) | The answer and a chart drawn only from rows a query actually returned. |
   | **Map** (right) | Power stations by fuel and capacity, states shaded by renewable share. Answers by region recolour the states; answers about power stations highlight them. |

3. Tick **Show reasoning** to see the agent's own notes between steps.

## The "What's in the data" tab

Use this tab to find out what you can ask. It's built from the WrenAI model itself, so it can't drift from what the agent sees.

![The data model tab with the Renewables topic traced](ui-data.png)

- **The diagram** shows three rows: **topics**, the **cubes** built for them, and the **tables** with every column and how they join. **Click a topic** to light up the cubes, tables and columns its questions use.
- **Hover a column** for its meaning and its real values.
- **The panel** lists the topics, example questions, and what the data **can't** answer. **How we define things** opens the business rules.

## Asking your own questions

The box under the answer runs the real agent on your question. The server behind it **sleeps when nobody's asking**: click **Start live questions** and wait about two minutes while it starts the whole lakehouse. Each visitor gets **2 live questions a day**, after an invisible bot check. The suggested questions always work.

## Updating the demo

The demo site is plain files in [`ui/`](../ui). GitHub Pages republishes it about a minute after every push to `main` that changes `ui/`.

1. **Refresh the data** (optional; it's a rolling 90 days): `make energy-data`
2. **Re-record:** `make demo-record` re-runs all 24 suggested questions through the real agent (about 15 minutes, roughly a dollar on Haiku 4.5). It checks each one still gets the expected kind of answer, and rebuilds the map data and the data catalog.
   - Only some questions: `uv run python scripts/demo_record.py --only price_by_region,wa_vs_nem`
   - Only the data tab (free, no AI): `uv run python scripts/demo_record.py --catalog-only`
3. **Check it locally:** `make demo-serve`, then open http://localhost:8765. (Opening `ui/index.html` from disk won't work: browsers block it from loading its data.)
4. **Publish:** commit `ui/data/` and push.

The suggested questions are in [`demos/energy-questions.yml`](../demos/energy-questions.yml) and the data tab's topics in [`demos/energy-topics.yml`](../demos/energy-topics.yml). Recording fails if either names something that doesn't exist in the model. For presenting from a terminal there's [`demos/energy.md`](../demos/energy.md).
