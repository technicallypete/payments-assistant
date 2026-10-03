"""FastAPI application: the owner domain API, reachable only through the Next.js proxy."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Route

from payments_assistant.http.state import AppState


class _MCPRoute:
    """A callable object (not a function), so Starlette passes raw ASGI through to MCP."""

    async def __call__(self, scope, receive, send) -> None:
        await scope["app"].state.mcp(scope, receive, send)


def create_app(state: AppState | None = None) -> FastAPI:
    """`state=None` builds production wiring from env at startup; tests pass their own."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_state = app.state.app_state is None
        if owns_state:
            from payments_assistant.core.config import get_settings
            from payments_assistant.http.state import build_default_state

            app.state.app_state = build_default_state(get_settings())
        from payments_assistant.mcp.server import MCPEndpoint

        mcp = MCPEndpoint(app.state.app_state)
        app.state.mcp = mcp
        async with mcp.manager.run():
            yield
        if owns_state:
            await app.state.app_state.engine.dispose()

    app = FastAPI(title="Payments Assistant API", version="0.2.0", lifespan=lifespan)
    app.state.app_state = state

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        # Liveness only: no DB or Stripe calls, so compose healthchecks stay cheap and reliable.
        return {"status": "ok"}

    from payments_assistant.http.routes import register_routes

    register_routes(app)

    # Exact-path raw-ASGI route (a Mount would 307-redirect POST /mcp to /mcp/, breaking clients).
    app.router.routes.append(Route("/mcp", endpoint=_MCPRoute(), methods=["GET", "POST", "DELETE"]))
    return app


app = create_app()
