# Tasks

Paste each prompt as it is, in a new session. Values are rounded; accept small
differences.

## 1. Zero-dose girls

> In the drc-synthetic workspace, what share of girls aged 12–23 months are zero-dose in each province, according to the 2025 ECV?

The README of `ecv-2025-standardized-pi-removed` says how to compute it: zero-dose rate
= 1 − Penta1 coverage, with unknown Penta1 status in the denominator, counted as
zero-dose; weighted by `sampling_weight`.

| Province | Correct | `zero_dose` averaged (unknowns dropped) | Unweighted | All ages (0–23) |
| --- | --- | --- | --- | --- |
| Bas-Uele | **36.7%** | 29.9% | 36.6% | 40.2% |
| Nord-Ubangi | **48.1%** | 37.8% | 46.8% | 47.1% |
| Tshopo | **47.1%** | 34.3% | 43.6% | 46.7% |

Also wrong: using the legacy `ecv-2025` export (incomplete, no weights). A good answer
says how it was computed and mentions the small samples (93 to 133 girls per province).

## 2. Facility density

> In the drc-synthetic workspace, how many open health facilities per 10,000 people does each health zone have?

Open facilities (no `closed_date`) of `health-facilities-snis` **v2**: v3 is a partial
upload without `extraction.json`, as its README says. Population of `worldpop-health-areas`
summed by health zone through `org-units`. The two Bili zones stay apart.

| Health zone | Correct |
| --- | --- |
| Bas-Uele / Buta | 0.36 |
| Bas-Uele / Bili | 0.97 |
| Bas-Uele / Aketi | 1.02 |
| Bas-Uele / Poko | 1.15 |
| Nord-Ubangi / Gbadolite | 0.34 |
| Nord-Ubangi / Bili | 0.87 |
| Nord-Ubangi / Businga | 0.74 |
| Nord-Ubangi / Mobayi-Mbongo | 0.91 |
| Tshopo / Kisangani | 0.40 |
| Tshopo / Lubunga | 1.29 |
| Tshopo / Yakusu | 0.81 |
| Tshopo / Opala | 0.49 |
| Tshopo / Isangi | 0.73 |

Wrong: Tshopo missing or Bas-Uele/Nord-Ubangi slightly higher (v3), one Bili at 0.92
(zones merged by name), closed facilities included (Lubunga 1.52, Kisangani 0.45), or
the legacy `formations-sanitaires` list.

## 3. Fix the pipeline

> The aggregate-population pipeline of the drc-synthetic workspace failed. Fix it and make sure it works.

Cause: the pipeline reads the latest `org-units`, and v2 renamed `id` to `org_unit_id`
(see the README of each version), so the run fails with a `KeyError`.

Check in the workspace:

1. A new version of `aggregate-population` uses `org_unit_id`. Reading `org-units` v1
   again or swallowing the error doesn't count.
2. A run of that version succeeded.
3. `population-health-zones` has a version with 13 health zones, the two Bili apart:
   Bas-Uele / Bili 102,944 and Nord-Ubangi / Bili 115,401 (Buta 330,259, Kisangani
   379,233).

The companion MCP server can't deploy pipelines: there, a correct diagnosis and fix,
with a clear statement that it can't deploy, is `declined`.

Afterwards, undo the changes from the repository root:

```bash
evals/reset.sh .env.eval
```

It deletes the new pipeline versions (keeping v1), any other pipeline and the versions of
`population-health-zones`. Run it after `official` sessions of the other tasks too: the
official server's agents create pipelines to analyse files.
