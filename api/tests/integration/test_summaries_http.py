"""Daily summary over HTTP: streaming, caching, refresh, failure, auth."""

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.models import DailySummary
from payments_assistant.core.services import summaries
from tests.demo_data import NOW
from tests.fake_llm import reply
from tests.http_fixtures import parse_sse

pytestmark = pytest.mark.integration

SUMMARY = "You took $822.00 today, $522.00 ahead of yesterday. Two cards bounced."


@pytest.fixture(autouse=True)
async def clean_today(admin_engine, harness):
    """Summaries are cached per (date, tz) globally; start each test from an empty cache."""
    async with async_sessionmaker(admin_engine)() as s, s.begin():
        await summaries.invalidate_today(s, now=NOW, tz=harness.state.settings.business_timezone)


async def _cached_rows(admin_engine) -> list[DailySummary]:
    async with async_sessionmaker(admin_engine)() as s:
        return list((await s.execute(select(DailySummary))).scalars())


def _text(events: list[dict]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "token")


async def test_first_post_streams_and_caches(owner_client, harness, admin_engine):
    harness.set_script(reply(SUMMARY))
    resp = await owner_client.post("/summaries/today")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "message_start" and types[-1] == "message_end"
    assert types.count("token") > 1  # streamed, not one blob
    assert _text(events) == SUMMARY
    assert events[-1]["text"] == SUMMARY

    rows = await _cached_rows(admin_engine)
    assert len(rows) == 1
    assert rows[0].summary == SUMMARY
    assert rows[0].summary_date.isoformat() == "2026-10-03"
    assert rows[0].stats["current"]["gross"] == "$822.00"


async def test_model_receives_tool_stats_not_raw_transactions(owner_client, harness):
    harness.set_script(reply(SUMMARY))
    await owner_client.post("/summaries/today")
    sent = harness.models[-1].seen[0]
    stats = json.loads(sent[-1].content)
    assert stats["current"]["gross"] == "$822.00"
    assert stats["previous"]["gross"] == "$300.00"
    assert stats["current"]["failures_by_reason"] == {"insufficient_funds": 2}
    assert stats["open_invoices"][0]["amount_remaining"] == "$1,200.00"
    assert "Penny" in sent[0].content


async def test_second_post_replays_cache_without_calling_model(owner_client, harness):
    harness.set_script(reply(SUMMARY))
    await owner_client.post("/summaries/today")
    calls_before = sum(len(m.seen) for m in harness.models)

    harness.set_script()  # any model call now would fail ("called more times than scripted")
    resp = await owner_client.post("/summaries/today")
    events = parse_sse(resp.text)
    assert [e["type"] for e in events] == ["message_start", "token", "message_end"]
    assert _text(events) == SUMMARY
    assert sum(len(m.seen) for m in harness.models) == calls_before


async def test_refresh_regenerates(owner_client, harness, admin_engine):
    harness.set_script(reply(SUMMARY))
    await owner_client.post("/summaries/today")

    harness.set_script(reply("Fresh take: still $822.00 today."))
    resp = await owner_client.post("/summaries/today", params={"refresh": "true"})
    assert _text(parse_sse(resp.text)) == "Fresh take: still $822.00 today."
    rows = await _cached_rows(admin_engine)
    assert len(rows) == 1 and rows[0].summary.startswith("Fresh take")


async def test_model_failure_reports_error_and_caches_nothing(owner_client, harness, admin_engine):
    harness.set_script()  # scripted model raises AssertionError on first call
    resp = await owner_client.post("/summaries/today")
    events = parse_sse(resp.text)
    assert [e["type"] for e in events] == ["message_start", "error", "message_end"]
    assert events[1]["code"] == "llm_error"
    assert await _cached_rows(admin_engine) == []


async def test_get_today_404_then_cached(owner_client, harness):
    resp = await owner_client.get("/summaries/today")
    assert resp.status_code == 404

    harness.set_script(reply(SUMMARY))
    await owner_client.post("/summaries/today")
    resp = await owner_client.get("/summaries/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == SUMMARY
    assert body["date"] == "2026-10-03"
    assert body["timezone"] == "America/New_York"


@pytest.mark.parametrize("method", ["post", "get"])
async def test_requires_auth(client, method):
    resp = await getattr(client, method)("/summaries/today")
    assert resp.status_code == 401


async def test_invalidate_today_removes_only_today(admin_engine, harness, owner_client):
    harness.set_script(reply(SUMMARY))
    await owner_client.post("/summaries/today")
    async with async_sessionmaker(admin_engine)() as s, s.begin():
        await summaries.invalidate_today(s, now=NOW, tz="America/New_York")
    async with async_sessionmaker(admin_engine)() as s:
        count = (await s.execute(select(func.count()).select_from(DailySummary))).scalar_one()
    assert count == 0
