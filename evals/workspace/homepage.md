# COD Nord-Est — health data

Health-system data of three provinces in the north-east of the Democratic Republic of
the Congo (Bas-Uele, Nord-Ubangi and Tshopo), prepared for analysis and mapping.

> This workspace holds synthetic data, generated for testing. It is realistic in
> structure but not in values.

## Data

| Data | Datasets |
| --- | --- |
| SNIS organisation units | `org-units` |
| Health facilities | `health-facilities-snis` |
| Population | `worldpop-health-areas`, `population-health-zones` |
| ECV 2025 vaccination survey | `ecv-2025-standardized-pi-removed`, `ecv-2025-aggregated` |

Each dataset version holds a `README.md`; standardized datasets also hold a
`metadata.json` describing each column. Read them before using the data.

## Pipelines

- `aggregate-population`: sums the WorldPop population of health areas by health zone
  and publishes it to `population-health-zones`.
