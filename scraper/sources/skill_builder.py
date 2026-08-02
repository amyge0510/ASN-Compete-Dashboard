"""AWS Skill Builder catalog via its public GraphQL search endpoint.

The skillbuilder.aws SPA is an empty React shell, but the search backend it
calls (POST https://skillbuilder.aws/graphql, operation getSearch/searchV2)
answers anonymously with the catalog an anonymous visitor sees (~2,250 items:
digital courses, labs, learning plans, exam prep). No Playwright, no login.

Anonymous queries are limited to a 100-result window (pageNumber × pageSize
<= 100), so full enumeration slices the catalog by facet filters (typeId,
then courseLevel, duration, domain) until every slice fits the window. Each
slice is fetched newest-first, so even if a slice can't be subdivided below
100 items, new courses still land in its first page; an oldest-first backstop
page covers slices up to 200. Filter shapes and sort enums were captured from
the site's own search UI (verified 2026-07-28).

If AWS changes the schema, parse errors surface in check-sources — and the
aws-training-blog feed still covers Skill Builder announcements.
"""
import json
import logging
import time

import requests

from scraper.fetch import USER_AGENT
from scraper.sources.course_catalog import Course

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://skillbuilder.aws/graphql"
WINDOW = 100          # anonymous result window: pageNumber * pageSize <= 100
DELAY_SECONDS = 0.5   # politeness between GraphQL calls

# Slicing order: typeId covers every item exactly once (facet counts sum to
# totalResults); the rest progressively subdivide the oversized types.
_SLICE_FACETS = ["typeId", "courseLevel", "duration", "domain"]

_QUERY = """query getSearch($input: SearchInputV2!) {
  searchV2(input: $input) {
    totalResults
    results {
      id
      item {
        id
        name
        trainingObjectUrl
        type { value }
      }
    }
  }
}"""

_FACETS_QUERY = """query getSearchFacets($input: SearchInputV2!) {
  searchV2(input: $input) {
    totalResults
    facets {
      name { id }
      values { value { id } totalResults }
    }
  }
}"""


def parse_search_page(payload: dict) -> tuple[int, list[dict]]:
    """Extract (totalResults, [{id, name, url, type}]) from one searchV2
    response. Raises ValueError on schema drift so check-sources notices."""
    try:
        search = payload["data"]["searchV2"]
        total = search["totalResults"]
        items = []
        for result in search["results"]:
            item = result.get("item") or {}
            items.append({
                "id": item.get("id") or result.get("id"),
                "name": item.get("name"),
                "url": item.get("trainingObjectUrl"),
                "type": (item.get("type") or {}).get("value", ""),
            })
        return total, items
    except (KeyError, TypeError) as e:
        raise ValueError(
            f"Unexpected Skill Builder GraphQL response shape: {e} — "
            f"payload starts: {json.dumps(payload)[:200]}") from e


def _post(operation: str, query: str, search_input: dict,
          timeout: float = 30.0) -> dict:
    time.sleep(DELAY_SECONDS)
    response = requests.post(
        GRAPHQL_URL,
        json={"operationName": operation,
              "variables": {"input": search_input},
              "query": query},
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _search(filters: list, order: str = "DESCENDING") -> tuple[int, list[dict]]:
    """One window-sized page of results for the given filters, date-sorted."""
    payload = _post("getSearch", _QUERY, {
        "pageNumber": 1, "pageSize": WINDOW, "language": "en-US",
        "sortCriterion": "DATE", "orderBy": order, "filters": filters,
    })
    return parse_search_page(payload)


def _facet_values(filters: list, facet_key: str) -> list[str]:
    """Non-empty value ids for one facet within the given slice."""
    payload = _post("getSearchFacets", _FACETS_QUERY, {
        "pageNumber": 1, "pageSize": 1, "language": "en-US", "filters": filters,
    })
    try:
        facets = payload["data"]["searchV2"]["facets"]
    except (KeyError, TypeError) as e:
        raise ValueError(f"Unexpected facets response shape: {e}") from e
    for facet in facets:
        if facet["name"]["id"] == facet_key:
            return [v["value"]["id"] for v in facet["values"]
                    if v["totalResults"] > 0]
    return []


def _collect(source: dict, filters: list, facet_idx: int,
             seen_ids: set, courses: list) -> None:
    total, items = _search(filters)
    _add(source, items, seen_ids, courses)
    if total <= WINDOW:
        return

    for idx in range(facet_idx, len(_SLICE_FACETS)):
        values = _facet_values(filters, _SLICE_FACETS[idx])
        if len(values) > 1:  # a single value can't shrink the slice
            for value in values:
                _collect(source,
                         filters + [{"key": _SLICE_FACETS[idx], "values": [value]}],
                         idx + 1, seen_ids, courses)
            return

    # No facet left to slice on: newest 100 are already in; add the oldest
    # 100 as a backstop. New items always surface (newest-first), but a
    # middle band of an unsliceable >200 slice goes unenumerated.
    _, oldest = _search(filters, order="ASCENDING")
    _add(source, oldest, seen_ids, courses)
    if total > 2 * WINDOW:
        logger.warning("[%s] slice %s has %d items — only newest/oldest 100 "
                       "enumerated", source["name"], filters, total)


def _add(source: dict, items: list[dict], seen_ids: set, courses: list) -> None:
    for item in items:
        # Stable key: the training-object URL (matches how other catalog
        # sources key on course URLs); fall back to the catalog id.
        cid = item["url"] or item["id"]
        if not cid or not item["name"] or cid in seen_ids:
            continue
        seen_ids.add(cid)
        courses.append(
            Course(
                source=source["name"],
                competitor=source.get("competitor", ""),
                course_id=cid,
                title=item["name"],
            )
        )


def ingest_skillbuilder(source: dict) -> list[Course]:
    """Enumerate the full catalog via facet-sliced windowed queries."""
    seen_ids: set = set()
    courses: list[Course] = []
    _collect(source, [], 0, seen_ids, courses)
    logger.debug("[%s] %d catalog items", source["name"], len(courses))
    if not courses:
        logger.warning("[%s] Skill Builder catalog yielded 0 items", source["name"])
    return courses
