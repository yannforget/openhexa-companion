# SNIS organisation units

Organisation units of the SNIS DHIS2 for Bas-Uele, Nord-Ubangi and Tshopo: the country,
provinces, health zones and health areas.

## Files

- `org_units.parquet`: one row per organisation unit.

## Columns

- `org_unit_id`: DHIS2 ID of the unit.
- `name`: name of the unit, as recorded in SNIS.
- `level`: `country`, `province`, `health_zone` or `health_area`.
- `parent_id`: DHIS2 ID of the parent unit; null for the country.

Health zone names are not unique: Bili exists in Bas-Uele and in Nord-Ubangi. Use IDs.
