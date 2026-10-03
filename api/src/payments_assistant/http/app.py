"""FastAPI application: the owner domain API, reachable only through the Next.js proxy."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Payments Assistant API", version="0.1.0")

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        # Liveness only: no DB or Stripe calls, so compose healthchecks stay cheap and reliable.
        return {"status": "ok"}

    return app


app = create_app()
