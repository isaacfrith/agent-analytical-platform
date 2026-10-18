# Domain: energy (Australian NEM, via Open Electricity)

NEM generation and market outcomes (Iceberg `energy` namespace via Trino). Prices and market value in AUD. Daily grain, NEM-local calendar days, 90-day rolling window ending yesterday.

Governed cubes: daily_generation, daily_price, renewable_share.

Units: energy MWh (additive — sum it); demand MW is a daily-average rate (average, never sum); demand_energy_mwh is the additive demand metric; prices AUD/MWh (a rate — average only, can go negative); emissions tCO2e (additive).

Renewable definition: `is_renewable` in fueltech_groups (wind/solar/hydro/bioenergy) — never enumerate techs in SQL. Share = renewable / total from the renewable_share cube.

Known collisions: `status` on facilities is unit lifecycle (operating|committed|commissioning|retired) — there is no second status table, so no ambiguity to resolve, just qualify it. `demand_mw` vs `demand_energy_mwh`: rate vs additive — state which you used. Capacity (facilities, SUM of registered MW) vs output (generation_daily, SUM of MWh): never mix them; capacity-vs-output questions need two queries.

Caveats to surface: battery `energy_mwh` goes negative when charging — correct behaviour, not dirty data. Commissioning units report <=90% of capacity.
