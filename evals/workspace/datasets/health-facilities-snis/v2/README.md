# Health facilities (SNIS)

Health facilities of the SNIS DHIS2 in Bas-Uele, Nord-Ubangi and Tshopo, extracted
weekly by the `extract-snis-facilities` pipeline.

## Files

- `facilities-<province>.parquet`: one file per province, one row per facility.
- `metadata.json`: label, description and type of each column.
- `extraction.json`: when the extraction ran and how many facilities it found. It is
  written last: a version without it is incomplete and must not be used.

## Rows and keys

Each row is a facility, identified by `org_unit_id` (SNIS DHIS2 ID). Closed facilities
are kept with a `closed_date`: leave them out to count open facilities.

Health zone names are not unique across provinces: use `health_zone_id`.

Coordinates are missing for about 5% of facilities.
