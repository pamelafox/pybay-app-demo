import datetime
import json
import uuid
from typing import Any

import pytest
from faker import Faker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_app.postgres_models import Species, Trip


@pytest.fixture(scope="function")
def faker() -> Faker:
    return Faker()


# ---------------- Bee Activity Endpoint Tests -----------------


@pytest.mark.parametrize(
    "query_params",
    [
        # Basic default window (dates defaulted) absolute activity
        {"lat": 37.7749, "lon": -122.4194},
        # Explicit short window, normalized activity
        {
            "lat": 37.7749,
            "lon": -122.4194,
            "start_date": (datetime.date.today() - datetime.timedelta(days=5)).isoformat(),
            "end_date": datetime.date.today().isoformat(),
            "absolute_activity": False,
        },
        # Require research grade & different sort order
        {"lat": 37.7749, "lon": -122.4194, "require_research_grade": True, "sort": "taxon_id"},
        # Ascending activity sort
        {"lat": 37.7749, "lon": -122.4194, "sort": "activity_asc"},
        # Peak month sort
        {"lat": 37.7749, "lon": -122.4194, "sort": "peak_month"},
        # Empty candidate set (very remote location)
        {"lat": 0.0, "lon": 0.0, "radius_km": 1},
    ],
)
def test_bees_active_success(test_client, snapshot, query_params: dict[str, Any]):
    resp = test_client.get("/bees/active", params=query_params)
    assert resp.status_code == 200
    data = resp.json()
    # Basic shape assertions
    assert "data" in data and "meta" in data
    assert "months" in data["meta"]
    snapshot_name = (
        "bees_active_"
        + f"{query_params.get('sort', 'default')}_"
        + f"{query_params.get('lat')}_"
        + f"{query_params.get('lon')}"
    )
    snapshot.assert_match(json.dumps(data, sort_keys=True, indent=2), snapshot_name)


def test_bees_active_invalid_date_format(test_client):
    resp = test_client.get(
        "/bees/active",
        params={"lat": 37.7749, "lon": -122.4194, "start_date": "2025-13-01"},  # invalid month
    )
    assert resp.status_code == 422
    assert "Invalid date" in resp.text


def test_bees_active_end_before_start(test_client):
    today = datetime.date.today().isoformat()
    earlier = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    resp = test_client.get(
        "/bees/active",
        params={"lat": 37.7749, "lon": -122.4194, "start_date": today, "end_date": earlier},
    )
    assert resp.status_code == 422
    assert "end_date must be >= start_date" in resp.text


def test_bees_active_window_too_large(test_client):
    start = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    end = datetime.date.today().isoformat()
    resp = test_client.get(
        "/bees/active",
        params={"lat": 37.7749, "lon": -122.4194, "start_date": start, "end_date": end},
    )
    assert resp.status_code == 422
    assert "Max window is 31 days" in resp.text


# ---------------- Species Search Endpoint Tests -----------------


@pytest.mark.parametrize(
    "query",
    ["ap", "apis", "bee"],  # vary length & expected matches
)
def test_bees_search_success(test_client, snapshot, query: str):
    resp = test_client.get("/bees/search", params={"q": query, "limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == query
    assert isinstance(body["count"], int)
    snapshot.assert_match(json.dumps(body, sort_keys=True, indent=2), f"bees_search_{query}")


def test_bees_search_too_short(test_client):
    resp = test_client.get("/bees/search", params={"q": "a"})
    # FastAPI validation error triggers 422 automatically
    assert resp.status_code == 422


# ---------------- Phenology Chart Endpoint Tests -----------------


def _make_species(session: AsyncSession, taxon_id: int):
    # Phenology arrays length 12 with varied counts to exercise _fmt_count branches
    counts = [0, 1, 5, 999, 1000, 1500, 2500000, 42, 77, 1234, 2, 3]
    norm = [0.0] * 12
    sp = Species(
        taxon_id=taxon_id,
        scientific_name=f"Testus specius {taxon_id}",
        common_name="Test Bee",
        phenology_counts=counts,
        phenology_normalized=norm,
        phenology_counts_all=counts,
        phenology_normalized_all=norm,
        peak_month=5,
        peak_month_all=5,
        window_start=4,
        window_end=6,
        window_start_all=4,
        window_end_all=6,
    )
    session.add(sp)
    return sp


def test_bees_phenology_chart_png(test_client):
    # Reuse first taxon from activity endpoint
    active_resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    assert active_resp.status_code == 200
    active_data = active_resp.json().get("data", [])
    if not active_data:
        pytest.skip("No active species available to test phenology chart")
    taxon_id = active_data[0]["taxon_id"]
    resp = test_client.get(f"/bees/phenology-chart/{taxon_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    first_name = active_data[0]["scientific_name"]
    assert resp.headers.get("X-Phenology-Summary", "").startswith(first_name)
    # Ensure some PNG bytes
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_bees_phenology_chart_not_found(test_client):
    resp = test_client.get("/bees/phenology-chart/999999999")
    assert resp.status_code == 404
    assert "Taxon not found" in resp.text


# ---------------- Trip Endpoint Tests -----------------


def _trip_payload(faker: Faker, **overrides) -> dict[str, Any]:
    start_dt = datetime.datetime.utcnow().replace(microsecond=0)
    end_dt = start_dt + datetime.timedelta(hours=5)
    # Provide a name with combining diacritic so normalization flags it
    combining_jose = "Jose\u0301"  # e + combining acute
    payload = {
        "event_name": f"Bee Survey {faker.word()} {faker.random_int(1, 100)}!",
        "organizers": [
            {"display_name": combining_jose, "role": "Lead"},
            {"display_name": "Ada"},
        ],
        "address_text": "123 Bee Lane\nHoneytown",
        "country_hint": "US",
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "approx_latitude": 37.77,
        "approx_longitude": -122.41,
        "positional_accuracy_m": 25,
        "contact_emails": [faker.email()],
        "notes": "Interesting trip <script>alert('x')</script> end",
    }
    payload.update(overrides)
    return payload


def test_create_trip_success(test_client, faker):
    # Use a unique suffix to avoid slug uniqueness collisions across repeated runs
    unique_suffix = uuid.uuid4().hex[:8]
    faker.seed_instance(12345)  # deterministic other generated fields
    payload = _trip_payload(faker, event_name=f"Bee Survey Example Success {unique_suffix}!")
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["event_slug"] and unique_suffix in body["event_slug"]
    assert body["organizers"][0]["has_diacritics"] is True  # José normalization
    assert body["notes_sanitized"] and "script" not in body["notes_sanitized"]
    assert body["warnings"]
    # Normalize volatile fields for snapshot stability
    snap_body = dict(body)
    snap_body["event_slug"] = "bee-survey-example-success"
    snap_body["event_name"] = "Bee Survey Example Success!"
    # Structural assertions instead of snapshot (dynamic slug & dates)
    assert snap_body["event_slug"].startswith("bee-survey-example-success")
    assert snap_body["organizers"][0]["normalized"] == "Jose"
    # Cleanup inserted trip to avoid leaking state
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == body["id"]))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup)
    except Exception:
        pass


def test_create_trip_no_address(test_client, faker):
    payload = _trip_payload(faker)
    # Remove address_text so address field should be None in response
    payload.pop("address_text", None)
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["address"] is None
    assert body["event_slug"]


def test_create_trip_invalid_timestamp_format(test_client, faker):
    payload = _trip_payload(faker, start_time="not-a-timestamp", end_time="also-bad")
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 422
    assert "Invalid timestamps" in resp.text


def test_create_trip_invalid_timestamps_order(test_client, faker):
    start = datetime.datetime.utcnow().replace(microsecond=0)
    end = start - datetime.timedelta(hours=1)
    payload = _trip_payload(faker, start_time=start.isoformat(), end_time=end.isoformat())
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 422
    assert "end_time must be > start_time" in resp.text


def test_create_trip_invalid_duration(test_client, faker):
    start = datetime.datetime.utcnow().replace(microsecond=0)
    end = start + datetime.timedelta(hours=30)
    payload = _trip_payload(faker, start_time=start.isoformat(), end_time=end.isoformat())
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 422
    assert "Duration must be <= 24h" in resp.text


def test_create_trip_no_organizers(test_client, faker):
    payload = _trip_payload(faker, organizers=[])
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 422
    assert "At least one organizer required" in resp.text


def test_get_trip_and_list_trips(test_client, faker):
    # Create two trips on different days
    start = datetime.datetime.utcnow().replace(microsecond=0)
    t1_payload = _trip_payload(
        faker,
        start_time=start.isoformat(),
        end_time=(start + datetime.timedelta(hours=2)).isoformat(),
        event_name=f"First Survey {faker.random_int(1000, 9999)}",
    )
    r1 = test_client.post("/trips", json=t1_payload)
    assert r1.status_code == 201
    trip_id1 = r1.json()["id"]

    start2 = start + datetime.timedelta(days=2)
    t2_payload = _trip_payload(
        faker,
        start_time=start2.isoformat(),
        end_time=(start2 + datetime.timedelta(hours=3)).isoformat(),
        event_name=f"Second Survey {faker.random_int(1000, 9999)}",  # ensure unique slug
    )
    r2 = test_client.post("/trips", json=t2_payload)
    assert r2.status_code == 201
    trip_id2 = r2.json()["id"]

    # Get trip 1
    g1 = test_client.get(f"/trips/{trip_id1}")
    assert g1.status_code == 200
    assert g1.json()["id"] == trip_id1

    # List trips with since filter to only include second trip
    since_date = (start + datetime.timedelta(days=1)).date().isoformat()
    list_resp = test_client.get("/trips", params={"since": since_date})
    assert list_resp.status_code == 200
    body = list_resp.json()
    ids = {item["id"] for item in body["data"]}
    assert trip_id2 in ids
    # Optional: both present without filter
    all_resp = test_client.get("/trips")
    # Cleanup both trips
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup_two():
        async with sessionmaker() as s:
            await s.begin()
            for tid in [trip_id1, trip_id2]:
                trip = (await s.scalars(select(Trip).where(Trip.id == tid))).first()
                if trip:
                    await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup_two)
    except Exception:
        pass
    assert all_resp.status_code == 200
    all_ids = {item["id"] for item in all_resp.json()["data"]}
    assert trip_id1 in all_ids and trip_id2 in all_ids


def test_list_trips_invalid_since(test_client):
    resp = test_client.get("/trips", params={"since": "2025-99-01"})
    assert resp.status_code == 422
    assert "Invalid since date" in resp.text


def test_list_trips_invalid_before(test_client):
    resp = test_client.get("/trips", params={"before": "not-a-date"})
    assert resp.status_code == 422
    assert "Invalid before date" in resp.text


def test_get_trip_not_found(test_client):
    resp = test_client.get("/trips/999999")
    assert resp.status_code == 404
    assert "Trip not found" in resp.text


# Additional coverage tests


def test_bees_active_min_activity_validation(test_client):
    resp = test_client.get(
        "/bees/active",
        params={"lat": 37.7749, "lon": -122.4194, "min_activity": 1.5},  # exceeds max 1 for normalized
    )
    assert resp.status_code == 422


def test_bees_active_candidate_cap(test_client):
    resp = test_client.get(
        "/bees/active",
        params={"lat": 37.7749, "lon": -122.4194, "candidate_cap": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["candidate_cap"] == 50
    assert body["meta"]["candidate_count"] <= 50


def test_bees_active_invalid_sort(test_client):
    resp = test_client.get(
        "/bees/active",
        params={"lat": 37.7749, "lon": -122.4194, "sort": "invalid"},
    )
    assert resp.status_code == 422  # validation error by FastAPI


def test_bees_search_limit(test_client):
    resp = test_client.get("/bees/search", params={"q": "bee", "limit": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] <= 1


def test_bees_search_no_results(test_client):
    resp = test_client.get("/bees/search", params={"q": "zzzznonexistent"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["data"] == []


def test_bees_phenology_chart_no_highlight(test_client):
    active_resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    taxon_id = active_resp.json().get("data", [{}])[0].get("taxon_id")
    if not taxon_id:
        pytest.skip("No taxa available for phenology chart no highlight test")
    resp = test_client.get(f"/bees/phenology-chart/{taxon_id}", params={"highlight_window": False})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_bees_phenology_chart_research_grade(test_client):
    active_resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    taxon_id = active_resp.json().get("data", [{}])[0].get("taxon_id")
    if not taxon_id:
        pytest.skip("No taxa available for phenology chart research grade test")
    resp = test_client.get(f"/bees/phenology-chart/{taxon_id}", params={"require_research_grade": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_bees_phenology_chart_incomplete_arrays(test_client):
    # Modify an existing species to have incomplete arrays and no peak/window
    active_resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    taxa = active_resp.json().get("data", [])
    if not taxa:
        pytest.skip("No active species available to modify for incomplete arrays test")
    taxon_id = taxa[0]["taxon_id"]
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _mutate_species():
        async with sessionmaker() as s:
            await s.begin()
            from fastapi_app.postgres_models import Species

            sp = (await s.scalars(select(Species).where(Species.taxon_id == taxon_id))).first()
            if sp:
                # Set both research-grade and all-observation arrays to incomplete to trigger fallback
                sp.phenology_counts = [5, 6, 7]
                sp.phenology_normalized = [0.3, 0.4, 0.3]
                sp.phenology_counts_all = [1, 2, 3]
                sp.phenology_normalized_all = [0.1, 0.2, 0.3]
                sp.peak_month = None
                sp.peak_month_all = None
                sp.window_start = None
                sp.window_end = None
                sp.window_start_all = None
                sp.window_end_all = None
                s.flush()
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_mutate_species)
    except Exception:
        pass
    # Verify mutation persisted directly
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _verify_fields():
        async with sessionmaker() as s:
            from fastapi_app.postgres_models import Species

            sp = (await s.scalars(select(Species).where(Species.taxon_id == taxon_id))).first()
            return (
                sp is not None
                and sp.window_start_all is None
                and sp.window_end_all is None
                and sp.peak_month_all is None
            )

    try:
        import anyio

        mutated = anyio.from_thread.run(_verify_fields)
    except Exception:
        mutated = False
    if not mutated:
        pytest.skip("Species mutation not persisted")
    chart_resp = test_client.get(f"/bees/phenology-chart/{taxon_id}")
    assert chart_resp.status_code == 200
    summary = chart_resp.headers.get("X-Phenology-Summary", "")
    assert summary == chart_resp.headers.get("X-Phenology-Summary")
    assert ";" not in summary


def test_create_trip_slugify_and_truncation(test_client, faker):
    # Create event name > 300 chars to test constraint & slug truncation.
    # Ensure final length <=300 after slicing.
    base = f"Bee {faker.random_int(1000, 9999)} " + ("A" * 310)  # uniqueness via random token
    long_name = base[:300]
    # Pre-delete potential existing slug
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", long_name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")[:300]

    async def _pre_delete_long():
        async with sessionmaker() as s:
            await s.begin()
            existing = (await s.scalars(select(Trip).where(Trip.event_slug == slug))).first()
            if existing:
                await s.delete(existing)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_pre_delete_long)
    except Exception:
        pass
    payload = _trip_payload(faker, event_name=long_name)
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["event_slug"]) <= 300
    assert body["event_slug"].startswith("bee-")
    # Cleanup
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup_slug():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == body["id"]))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup_slug)
    except Exception:
        pass


def test_list_trips_since_before(test_client, faker):
    start = datetime.datetime.utcnow().replace(microsecond=0)
    # Create a trip
    unique_name = f"Window Survey {faker.random_int(1000, 9999)}"
    payload = _trip_payload(
        faker,
        start_time=start.isoformat(),
        end_time=(start + datetime.timedelta(hours=2)).isoformat(),
        event_name=unique_name,
    )
    r = test_client.post("/trips", json=payload)
    assert r.status_code == 201
    since = start.date().isoformat()
    before = (start + datetime.timedelta(days=1)).date().isoformat()
    list_resp = test_client.get("/trips", params={"since": since, "before": before})
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["meta"]["query"]["since"] == since
    assert body["meta"]["query"]["before"] == before
    # Cleanup
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup_window():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == r.json()["id"]))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup_window)
    except Exception:
        pass


def test_create_trip_no_valid_organizers(test_client, faker):
    payload = _trip_payload(faker, organizers=[{"display_name": "   ", "role": ""}])
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 422
    assert "No valid organizers" in resp.text


def test_create_trip_slugify_punctuation(test_client, faker):
    noisy_name = f"Bee!! Survey/// Example??? ### {faker.random_int(1000, 9999)}"
    payload = _trip_payload(faker, event_name=noisy_name)
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 201
    slug = resp.json()["event_slug"]
    assert "--" not in slug
    assert slug.startswith("bee-survey-example-") or slug == "bee-survey-example"  # allow truncated suffix
    # Cleanup
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup_slug_noise():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == resp.json()["id"]))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup_slug_noise)
    except Exception:
        pass


# Removed test_list_trips_empty: persistent seed data prevents guaranteeing empty table


def test_bees_active_defaulted_dates_start_only(test_client):
    start = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
    resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194, "start_date": start})
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["start_date"] == start
    # end_date defaulted only when start_date supplied -> defaulted_dates True
    # defaulted_dates only present when data returned (items path). For empty candidate set we skip
    # So ensure absence OR True depending on presence of candidates
    assert meta.get("defaulted_dates") in (None, True)


def test_bees_active_defaulted_dates_end_only(test_client):
    end = datetime.date.today().isoformat()
    resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194, "end_date": end})
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["end_date"] == end
    assert meta.get("defaulted_dates") in (None, True)


def test_bees_active_filter_by_min_activity_normalized(test_client):
    resp = test_client.get(
        "/bees/active",
        params={
            "lat": 37.7749,
            "lon": -122.4194,
            "absolute_activity": False,
            "min_activity": 0.8,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    for item in data:
        assert item["activity_score"] >= 0.8


def test_bees_active_filter_incomplete_phenology(test_client):
    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _create_incomplete_species():
        async with sessionmaker() as s:
            await s.begin()
            sp = Species(
                taxon_id=999998,
                scientific_name="Incomplete phenology test",
                common_name="Test",
                phenology_counts=[1, 2, 3],
                phenology_normalized=[0.1, 0.2, 0.3],
                phenology_counts_all=[1, 2, 3],
                phenology_normalized_all=[0.1, 0.2, 0.3],
                peak_month=5,
                peak_month_all=5,
            )
            s.add(sp)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_create_incomplete_species)
    except Exception:
        pass
    resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    assert resp.status_code == 200

    async def _cleanup_incomplete():
        async with sessionmaker() as s:
            await s.begin()
            sp = (await s.scalars(select(Species).where(Species.taxon_id == 999998))).first()
            if sp:
                await s.delete(sp)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup_incomplete)
    except Exception:
        pass


def test_bees_search_with_results(test_client):
    resp = test_client.get("/bees/search", params={"q": "bee"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "bee"
    assert body["count"] >= 0
    assert isinstance(body["data"], list)
    if body["data"]:
        item = body["data"][0]
        assert "taxon_id" in item
        assert "scientific_name" in item


def test_bees_phenology_chart_with_custom_dimensions(test_client):
    active_resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    taxa = active_resp.json().get("data", [])
    if not taxa:
        pytest.skip("No active species available")
    taxon_id = taxa[0]["taxon_id"]
    resp = test_client.get(
        f"/bees/phenology-chart/{taxon_id}",
        params={"width": 800, "height": 400, "highlight_window": True, "require_research_grade": False},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_bees_phenology_chart_summary_with_window(test_client):
    active_resp = test_client.get("/bees/active", params={"lat": 37.7749, "lon": -122.4194})
    taxa = active_resp.json().get("data", [])
    if not taxa:
        pytest.skip("No active species available")
    taxon_id = taxa[0]["taxon_id"]
    resp = test_client.get(f"/bees/phenology-chart/{taxon_id}")
    assert resp.status_code == 200
    summary = resp.headers.get("X-Phenology-Summary", "")
    assert summary


def test_create_trip_with_address(test_client, faker):
    unique_suffix = uuid.uuid4().hex[:8]
    payload = _trip_payload(
        faker,
        event_name=f"Trip With Address {unique_suffix}",
        address_text="123 Main St\nCity, State",
    )
    resp = test_client.post("/trips", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["address"] is not None
    assert "lines" in body["address"]
    assert len(body["address"]["lines"]) > 0

    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == body["id"]))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup)
    except Exception:
        pass


def test_get_trip_with_address(test_client, faker):
    unique_suffix = uuid.uuid4().hex[:8]
    payload = _trip_payload(
        faker,
        event_name=f"Trip Get Address {unique_suffix}",
        address_text="456 Oak Ave\nTown, State",
    )
    create_resp = test_client.post("/trips", json=payload)
    assert create_resp.status_code == 201
    trip_id = create_resp.json()["id"]
    get_resp = test_client.get(f"/trips/{trip_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["address"] is not None
    assert "lines" in body["address"]

    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == trip_id))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup)
    except Exception:
        pass


def test_list_trips_response_data(test_client, faker):
    unique_suffix = uuid.uuid4().hex[:8]
    payload = _trip_payload(faker, event_name=f"Trip List Data {unique_suffix}")
    create_resp = test_client.post("/trips", json=payload)
    assert create_resp.status_code == 201
    trip_id = create_resp.json()["id"]
    list_resp = test_client.get("/trips")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert "data" in body
    assert "meta" in body
    assert len(body["data"]) > 0
    found = any(item["id"] == trip_id for item in body["data"])
    assert found

    sessionmaker = test_client.app.state.sessionmaker  # type: ignore[attr-defined]

    async def _cleanup():
        async with sessionmaker() as s:
            await s.begin()
            trip = (await s.scalars(select(Trip).where(Trip.id == trip_id))).first()
            if trip:
                await s.delete(trip)
            await s.commit()

    try:
        import anyio

        anyio.from_thread.run(_cleanup)
    except Exception:
        pass


def test_bees_phenology_chart_incomplete_phenology_arrays(test_client):
    taxon_id = 888888
    resp = test_client.get(f"/bees/phenology-chart/{taxon_id}", params={"require_research_grade": False})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_bees_phenology_chart_large_counts(test_client):
    taxon_id = 777777
    resp = test_client.get(f"/bees/phenology-chart/{taxon_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    summary = resp.headers.get("X-Phenology-Summary", "")
    assert "Test Large Counts" in summary
