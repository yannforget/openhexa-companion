# ECV 2025 — Indicators

Vaccination coverage estimates from the 2025 round of the ECV (*Enquête de couverture
vaccinale*), computed from `ecv-2025-standardized-pi-removed`.

## Files

- `ecv-2025-indicators.parquet`: one row per geographic unit, area of residence, age
  group and indicator.
- `metadata.json`: label, description and type of each column.

## Rows and keys

The key is (`level`, `province_id`, `health_zone_id`, `residence_area`, `age_group`,
`indicator`). Levels are `country`, `province` and `health_zone`; the urban and rural
breakdown is given for the country and provinces only. Age groups are 6–23 and 12–23
months.

## Indicators

- **Coverage** (`penta1`, `penta2`, `penta3`, `mcv1`, `mcv2`): share of children who
  received the dose, by card or recall. Children whose status is unknown count as not
  vaccinated.
- **`zero_dose`**: share of children without Penta1, i.e. `1 - penta1`.
- **`dropout_penta1_penta3`**: share of children who received Penta1 but not Penta3. The
  denominator is the children who received Penta1.

Estimates are proportions from 0 to 1.

## Method

Estimates use the survey design: sampling weights, health zones as strata and health
areas as clusters. Standard errors use linearization; 95% confidence intervals are
computed on the logit scale. A health zone with a single sampled health area is centred
on the overall mean.

Health zone estimates rest on a few health areas, so their confidence intervals are
wide. Estimates on fewer than 25 children are suppressed
(`flag = suppressed`); estimates on 25 to 50 children are flagged as
`unstable`. The confidence interval is null when the subgroup has no degrees of freedom.

## Known issues

- MCV2 is due at 15 months: many children aged 12–23 months are not yet eligible, so
  `mcv2` is low by design.
