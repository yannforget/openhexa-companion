import json
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from openhexa.companion import CatalogItem, Client, InvalidInputError, ItemType, search


def _item(reference: str, name: str = "", kind: ItemType = "dataset", **fields: Any) -> CatalogItem:
    return CatalogItem(
        type=kind, reference=reference, name=name or reference, slug=reference, **fields
    )


def _found(items: list[CatalogItem], query: str) -> list[str]:
    return [hit.item.reference for hit in search(items, query).hits]


def test_words_match_prefixes_without_case_or_accents():
    items = [
        _item("donnees-palu", "Données paludisme"),
        _item("malariaCases", kind="pipeline"),
        _item("other", "Autre", description="Nothing relevant"),
    ]

    assert _found(items, "DONNEES") == ["donnees-palu"]
    assert _found(items, "palu") == ["donnees-palu"]
    assert _found(items, "cases") == ["malariaCases"]
    assert set(_found(items, "malaria paludisme")) == {"malariaCases", "donnees-palu"}


def test_ranking_prefers_phrase_then_all_words_then_names():
    items = [
        _item("described", "Stock", description="malaria cases by district"),
        _item("some", "Malaria"),
        _item("both", "Cases of malaria"),
        _item("phrase", "Malaria cases"),
        _item("tagged", "Inventory", kind="pipeline", tags=["malaria"]),
    ]

    assert _found(items, "malaria cases")[:3] == ["phrase", "both", "described"]
    assert _found(items, "malaria")[-1] == "described"


def test_ties_go_to_own_then_recent_items():
    old, new = datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    items = [
        _item("other/shared", "Population", shared=True, workspace="other", updated_at=new),
        _item("old", "Population", updated_at=old),
        _item("new", "Population", updated_at=new),
    ]

    assert _found(items, "population") == ["new", "old", "other/shared"]
    assert _found(items, "") == ["other/shared", "new", "old"]


@pytest.mark.parametrize("query", ['"', "malaria AND", "-malaria", "*", "NEAR(a b)", "a:b ^c"])
def test_query_syntax_has_no_meaning(query: str):
    items = [_item("cases", "Malaria cases")]

    search(items, query)


def test_types_filter_and_counts():
    items = [_item(f"cases-{i}") for i in range(3)] + [_item("cases", kind="table")]

    result = search(items, "cases", types=["dataset"], limit=2)
    everything = search(items, "cases", limit=2)

    assert len(result.hits) == 2
    assert result.matches == {"dataset": 3}
    assert result.searched == 3
    assert everything.matches == {
        "dataset": 3,
        "pipeline": 0,
        "webapp": 0,
        "query": 0,
        "table": 1,
    }
    with pytest.raises(InvalidInputError, match="'file'"):
        search(items, "cases", types=["file"])


def test_extract_marks_words_of_matching_descriptions():
    items = [_item("a", "Cases", description="Weekly malaria cases reported by districts.")]

    hit = search(items, "district")

    assert hit.hits[0].extract == "Weekly malaria cases reported by **districts**."
    assert search(items, "cases").hits[0].extract is not None
    assert search([_item("b", "Cases")], "cases").hits[0].extract is None


def test_catalog_reads_shared_datasets_and_survives_a_failing_database():
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "database" in body["query"]:
            return httpx2.Response(200, json={"errors": [{"message": "database unavailable"}]})
        dataset = {
            "id": "1",
            "slug": "pop",
            "name": "Population",
            "description": None,
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        return httpx2.Response(
            200,
            json={
                "data": {
                    "workspace": {
                        "slug": "ws",
                        "datasets": {
                            "items": [
                                {"dataset": dataset | {"workspace": {"slug": "ws"}}},
                                {"dataset": dataset | {"workspace": {"slug": "other"}}},
                            ]
                        },
                        "savedQueries": {
                            "items": [
                                {
                                    "slug": "pop-by-region",
                                    "name": "Population by region",
                                    "description": None,
                                    "updatedAt": "2026-01-01T00:00:00Z",
                                }
                            ]
                        },
                    },
                    "pipelines": {"items": []},
                    "webapps": {"items": []},
                }
            },
        )

    with Client(
        "https://openhexa.test", "ws", "t", http_transport=httpx2.MockTransport(handle)
    ) as client:
        catalog = client.catalog()
        only_datasets = client.catalog(["dataset"])

    assert [(i.type, i.reference, i.shared) for i in catalog.items] == [
        ("dataset", "pop", False),
        ("dataset", "other/pop", True),
        ("query", "pop-by-region", False),
    ]
    assert "database unavailable" in catalog.errors["table"]
    assert only_datasets.errors == {}
    assert len(requests) == 3
    assert requests[-1]["variables"] == {
        "workspace": "ws",
        "perPage": 10_000,
        "datasets": True,
        "pipelines": False,
        "webapps": False,
        "queries": False,
    }


@pytest.mark.workspace
def test_catalog_of_demo_workspace(client: Client):
    catalog = client.catalog()

    assert catalog.errors == {}
    assert {item.type for item in catalog.items} >= {"dataset", "pipeline"}
