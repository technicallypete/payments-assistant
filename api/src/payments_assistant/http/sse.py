"""Server-Sent Events framing for agent events (spec §6.1)."""

import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # tell any proxy not to buffer
    "Connection": "keep-alive",
}


def sse_frame(event: BaseModel) -> bytes:
    payload = event.model_dump(mode="json")
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()


def sse_response(frames: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(frames, media_type="text/event-stream", headers=SSE_HEADERS)
