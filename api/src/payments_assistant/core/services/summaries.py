"""Daily summary: today's aggregates, narrated by the LLM, cached per (local date, timezone).

The numbers come from the same `get_activity` tool the chat agent uses, so the summary and the chat
can never disagree. The model only writes prose around a JSON blob of pre-formatted figures.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import date, datetime
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.agents.events import (
    AgentEvent,
    ErrorEvent,
    MessageEnd,
    MessageStart,
    Token,
)
from payments_assistant.core.agents.loop import _text
from payments_assistant.core.models import DailySummary
from payments_assistant.core.timeutil import Period, local_today
from payments_assistant.core.tools import REGISTRY, ToolContext
from payments_assistant.core.tools.owner import ActivityIn, ActivityOut

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """You are Penny, the payments assistant for a small business on Stripe. Write the
owner's daily summary from the JSON you're given.

- 2 to 4 sentences of plain, warm, specific prose. Lead with the headline: what came in today
  so far and how that compares with yesterday at the same time.
- Mention failed payments and why (e.g. insufficient funds) if there were any.
- Mention open invoices worth chasing (who and how much), biggest first. Say if any are overdue.
- Every number must come from the JSON, quoted as written. Never calculate, round or invent
  figures.
- No transaction lists, no bullet points, no IDs. Light personality is welcome; filler isn't.

Example of the tone: "You took $4,280 across 18 payments today, well ahead of yesterday. Two cards
were declined for insufficient funds, and there's still an unpaid $1,200 invoice sitting with Acme
Corp."
"""


async def build_stats(ctx: ToolContext) -> ActivityOut:
    """Today's activity vs yesterday-so-far plus open invoices, via the shared `get_activity`
    tool."""
    result = await REGISTRY["get_activity"].fn(ctx, ActivityIn(period=Period.TODAY))
    assert isinstance(result, ActivityOut)
    return result


async def _cached(session: AsyncSession, day: date, tz: str) -> DailySummary | None:
    return (
        await session.execute(
            select(DailySummary).where(
                DailySummary.summary_date == day, DailySummary.timezone == tz
            )
        )
    ).scalar_one_or_none()


async def get_cached(session: AsyncSession, *, now: datetime, tz: str) -> DailySummary | None:
    return await _cached(session, local_today(now, ZoneInfo(tz)), tz)


async def stream_daily_summary(
    session: AsyncSession,
    *,
    model: BaseChatModel,
    model_name: str,
    ctx: ToolContext,
    force: bool = False,
) -> AsyncIterator[AgentEvent]:
    tz_name = ctx.settings.business_timezone
    day = local_today(ctx.now, ctx.tz)

    if not force and (cached := await _cached(session, day, tz_name)) is not None:
        yield MessageStart(model=cached.llm_model)
        yield Token(delta=cached.summary)
        yield MessageEnd(text=cached.summary, input_tokens=0, output_tokens=0, tool_records=[])
        return

    stats = await build_stats(ctx)
    stats_json = stats.model_dump(mode="json")
    yield MessageStart(model=model_name)

    parts: list[str] = []
    usage_in = usage_out = 0
    try:
        async for chunk in model.astream(
            [SystemMessage(SUMMARY_PROMPT), HumanMessage(json.dumps(stats_json))]
        ):
            delta = _text(chunk.content)
            if delta:
                parts.append(delta)
                yield Token(delta=delta)
            if chunk.usage_metadata:
                usage_in += chunk.usage_metadata.get("input_tokens", 0)
                usage_out += chunk.usage_metadata.get("output_tokens", 0)
    except Exception as exc:  # provider failure: report, keep partial text, don't cache
        logger.exception("daily summary generation failed")
        yield ErrorEvent(code="llm_error", message=f"Couldn't write the summary: {exc}"[:300])
        yield MessageEnd(
            text="".join(parts), input_tokens=usage_in, output_tokens=usage_out, tool_records=[]
        )
        return

    text = "".join(parts).strip()
    if text:
        await session.execute(
            insert(DailySummary)
            .values(
                summary_date=day,
                timezone=tz_name,
                stats=stats_json,
                summary=text,
                llm_model=model_name,
                generated_at=ctx.now,
            )
            .on_conflict_do_update(
                index_elements=[DailySummary.summary_date, DailySummary.timezone],
                set_={
                    "stats": stats_json,
                    "summary": text,
                    "llm_model": model_name,
                    "generated_at": ctx.now,
                },
            )
        )
    yield MessageEnd(text=text, input_tokens=usage_in, output_tokens=usage_out, tool_records=[])


async def invalidate_today(session: AsyncSession, *, now: datetime, tz: str) -> None:
    """Drop today's cached summary (called when new Stripe activity arrives via webhook)."""
    await session.execute(
        delete(DailySummary).where(
            DailySummary.summary_date == local_today(now, ZoneInfo(tz)),
            DailySummary.timezone == tz,
        )
    )
