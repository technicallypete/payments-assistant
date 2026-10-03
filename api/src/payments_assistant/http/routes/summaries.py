"""Daily summary endpoints. POST streams (SSE); GET returns the cached summary, if any."""

from collections.abc import AsyncIterator
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from payments_assistant.core.services import summaries
from payments_assistant.core.tools import ToolContext
from payments_assistant.http.deps import Owner, State
from payments_assistant.http.sse import sse_frame, sse_response

router = APIRouter(prefix="/summaries", tags=["summaries"])


class SummaryOut(BaseModel):
    date: date
    timezone: str
    summary: str
    generated_at: datetime


@router.post("/today", response_class=StreamingResponse)
async def stream_today(state: State, owner: Owner, refresh: bool = False) -> StreamingResponse:
    """Stream today's summary as SSE. Cached summaries replay as a single token event;
    `refresh=true` regenerates."""

    async def frames() -> AsyncIterator[bytes]:
        # Own session: the request-scoped dependency is closed before the body streams.
        async with state.sessionmaker() as session, session.begin():
            ctx = ToolContext(
                session=session,
                settings=state.settings,
                now=state.clock(),
                owner_id=owner.owner_id,
                owner_gateway=state.owner_gateway(),
            )
            async for event in summaries.stream_daily_summary(
                session,
                model=state.model("summary"),
                model_name=state.model_name("summary"),
                ctx=ctx,
                force=refresh,
            ):
                yield sse_frame(event)

    return sse_response(frames())


@router.get("/today", response_model=SummaryOut)
async def get_today(state: State, owner: Owner) -> SummaryOut:
    async with state.sessionmaker() as session:
        cached = await summaries.get_cached(
            session, now=state.clock(), tz=state.settings.business_timezone
        )
    if cached is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No summary for today yet.")
    return SummaryOut(
        date=cached.summary_date,
        timezone=cached.timezone,
        summary=cached.summary,
        generated_at=cached.generated_at,
    )
