"""Route modules. Each exposes `router`; `register_routes` mounts them all."""

from importlib import import_module

from fastapi import FastAPI

# Modules are imported lazily by name so parallel work can add files without merge conflicts.
ROUTE_MODULES = [
    "auth",
    "conversations",
    "actions",
    "summaries",
    "handoffs",
    "customers",
    "webhooks",
]


def register_routes(app: FastAPI) -> None:
    for name in ROUTE_MODULES:
        try:
            module = import_module(f"payments_assistant.http.routes.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"payments_assistant.http.routes.{name}":
                continue  # not written yet
            raise
        app.include_router(module.router)
