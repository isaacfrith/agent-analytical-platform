# Energy business rules (NEM, via Open Electricity)

## Regions
- Five NEM regions: NSW1 | QLD1 | SA1 | TAS1 | VIC1 (state-scale zones).
  Always qualify which region a number belongs to.

## Units
- Energy MWh (additive — sum it). Power MW and demand MW are daily-average
  rates — average, never sum. Prices AUD/MWh (a rate — average only, can go
  negative). Emissions tCO2e (additive). Market value AUD (additive).

## Renewable definition (governed)
- Renewable = `is_renewable` in fueltech_groups (wind/solar/hydro/bioenergy).
  Batteries and pumps STORE energy; they are not generation. Never enumerate
  techs in SQL — join the mapping table or use the renewable_share cube.

## Batteries
- Battery `energy_mwh` goes NEGATIVE when charging. This is correct NEM
  behaviour, not dirty data. Do not filter negatives out.

## Statuses
- facilities.status: operating | committed | commissioning | retired.
  Commissioning units report <=90% of capacity. Capacity questions SUM
  capacity_registered_mw; output questions belong to generation_daily.

## Grain and window
- Daily grain, NEM-local calendar days, 90-day rolling window ending
  yesterday. `facilities` is unit grain (site columns carried).
- No facility-level output table exists yet: capacity-vs-output questions
  need two queries (capacity from facilities, output from generation_daily).

## Canonical joins
- generation_daily → fueltech_groups on fueltech; facilities →
  fueltech_groups on fueltech; generation_daily → market_daily on
  network_region + day.
