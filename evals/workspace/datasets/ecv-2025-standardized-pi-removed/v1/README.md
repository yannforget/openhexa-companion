# ECV 2025 — standardized data (PI removed)

The ECV (*Enquête de couverture vaccinale*) is a vaccination coverage survey in the
Democratic Republic of the Congo. The 2025 round covers children aged 0 to 23 months
in Bas-Uele, Nord-Ubangi and Tshopo; fieldwork took place in September and October 2025.

Personal information has been removed.

## Files

- `ecv-2025.parquet`: the data, one row per child.
- `metadata.json`: label, description, type and personal-information flag of each
  column.

## Rows and keys

Each row is a child. Household and mother/caregiver answers are repeated on each child's
row: deduplicate on `household_id` or `caregiver_id` for household or caregiver
statistics. `record_id` is unique within the round. To combine rounds, stack them and
use (`survey_round`, `record_id`) as the key. Age ranges, questions and answer options
change between rounds, so check each column's description before comparing rounds.

## Vaccination

`received_*` columns combine the vaccination card and the caregiver's recall: true means
received, false means not received and null means unknown. Compute crude coverage with
unknowns in the denominator, counted as not vaccinated:

```text
coverage = received / (received + not received + unknown)
```

A zero-dose child is a child who has not received Penta1. `zero_dose` is the opposite of
`received_penta1`, so it is null when Penta1 is unknown. Don't average `zero_dose`:
unknowns would leave the denominator. Compute the zero-dose rate as `1 - Penta1
coverage`, so that children with an unknown status count as zero-dose:

```text
zero-dose rate = (not received + unknown) / (received + not received + unknown)
```

MCV1 is due at 9 months and MCV2 at 15 months: compute coverage on children aged 12–23
months, or MCV2 will look low by design.

## Survey design

Use `sampling_weight` for estimates. Health zones are the strata and health areas the
clusters. The survey is not representative at health-area level. Ready-made estimates
with confidence intervals are in `ecv-<round>-aggregated` when it exists.

## Geography

`province_id` and `health_zone_id` are SNIS DHIS2 organisation-unit IDs. Province, health
zone and health area names are kept as recorded by the survey. Health zone names repeat
across provinces (Bili): group by `health_zone_id`.
