# WorldPop population of health areas

Modelled population of 2025, from WorldPop Global2 R2025A (top-down, constrained), summed
within the polygons of SNIS health areas.

## Files

- `population.parquet`: one row per health area, with `org_unit_id` (SNIS DHIS2 ID) and
  `population`.

Population is modelled, not counted: use it for orders of magnitude and denominators,
not as a census.
