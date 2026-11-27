"""Sum the WorldPop population of health areas by health zone."""

import io
from datetime import UTC, datetime

import pandas as pd
from openhexa.sdk import current_run, parameter, pipeline, workspace

# Dataset this pipeline publishes to.
OUTPUT = "population-health-zones"


@pipeline("aggregate-population", timeout=1800)
@parameter(
    "publish",
    type=bool,
    default=True,
    help="Publish a new version of population-health-zones. Otherwise only log a summary.",
)
def aggregate_population(publish: bool):
    zones = aggregate()
    if publish:
        publish_zones(zones)
    else:
        current_run.log_info(
            f"Not published: {len(zones)} health zones, {zones['population'].sum():.0f} people"
        )


@aggregate_population.task
def aggregate() -> pd.DataFrame:
    units = read_parquet("org-units", "org_units.parquet")
    population = read_parquet("worldpop-health-areas", "population.parquet")
    zones = units[units["level"] == "health_zone"][["id", "name", "parent_id"]]
    areas = units[units["level"] == "health_area"][["id", "parent_id"]]
    provinces = units[units["level"] == "province"][["id", "name"]]
    areas = areas.merge(population, left_on="id", right_on="org_unit_id", how="left")
    totals = areas.groupby("parent_id", as_index=False)["population"].sum()
    zones = zones.merge(totals, left_on="id", right_on="parent_id", how="left", suffixes=("", "_"))
    zones = zones.merge(provinces, left_on="parent_id", right_on="id", suffixes=("", "_province"))
    result = zones.rename(
        columns={"id": "health_zone_id", "name": "health_zone", "name_province": "province"}
    )[["province", "health_zone", "health_zone_id", "population"]]
    current_run.log_info(f"Aggregated {len(areas)} health areas into {len(result)} health zones")
    return result.sort_values(["province", "health_zone"])


def read_parquet(dataset: str, filename: str) -> pd.DataFrame:
    version = workspace.get_dataset(dataset).latest_version
    current_run.log_info(f"Reading {dataset} {version.name}/{filename}")
    return pd.read_parquet(io.BytesIO(version.get_file(filename).read()))


@aggregate_population.task
def publish_zones(zones: pd.DataFrame) -> None:
    name = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    version = workspace.get_dataset(OUTPUT).create_version(name)
    version.add_file(io.BytesIO(zones.to_parquet(index=False)), "population.parquet")
    current_run.log_info(f"Published {OUTPUT} version {name}")
