# Domain: energy (Australian NEM + WA's WEM, via Open Electricity)

Generation and market outcomes for two separate, unconnected markets (Iceberg `energy` namespace via Trino): the NEM (east coast; regions NSW1|QLD1|SA1|TAS1|VIC1) and the WEM (south-west WA; one region, WEM). Every fact row has `network` (NEM|WEM). "The NEM" means filter `network = 'NEM'` — WEM rows never belong in NEM totals or averages; "Australia"/"national" means both, reported per network. Northern Territory is not covered — say so if asked. Prices and market value in AUD. Daily grain, market-local calendar days (NEM +10, WEM +08), 90-day rolling window ending yesterday.

Governed cubes: daily_generation, daily_price, renewable_share.

Units: energy MWh (additive — sum it); demand MW is a daily-average rate (average, never sum); demand_energy_mwh is the additive demand metric; prices AUD/MWh (a rate — average only, can go negative); emissions tCO2e (additive).

Renewable definition: `is_renewable` in fueltech_groups (wind/solar/hydro/bioenergy) — never enumerate techs in SQL. Share = renewable_share_pct from the renewable_share cube.

Fuel families (coal, gas, solar, wind, hydro, battery) are `fueltech_group` values in fueltech_groups — filter on the group via the mapping, never a hand-picked fueltech list ("coal" is coal_black AND coal_brown; Victoria's coal is brown).

Known collisions: `status` on facilities is unit lifecycle (operating|committed|commissioning|retired) — there is no second status table, so no ambiguity to resolve, just qualify it. `demand_mw` vs `demand_energy_mwh`: rate vs additive — state which you used. Capacity (facilities, SUM of registered MW) vs output (generation_daily, SUM of MWh): never mix them; capacity-vs-output questions need two queries.

Caveats to surface: battery `energy_mwh` goes negative when charging — correct behaviour, not dirty data. Commissioning units report <=90% of capacity.
