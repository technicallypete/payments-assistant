"""Owner MCP server (bonus feature, spec §2.5): the owner tools over MCP for Claude Desktop/Code.

- Same tool definitions as the web agent (from the registry), so schemas can't drift.
- Auth: owner API keys (`Authorization: Bearer pak_...`) only; session tokens are rejected.
- Mutations stay proposals; `confirm_action` (destructive hint, so clients ask the human) executes
  one, and only with the key that proposed it. Audited as `owner_mcp`.
- Stateless streamable HTTP with JSON responses: each request is self-contained.
"""

import json
import logging
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from payments_assistant.core.services import actions, api_keys
from payments_assistant.core.tools import ToolContext, tools_for
from payments_assistant.core.tools.registry import ToolError, ToolSpec
from payments_assistant.http.state import AppState

logger = logging.getLogger(__name__)

# Set by the auth wrapper for the duration of one HTTP request; handlers read it.
_principal: ContextVar[api_keys.KeyPrincipal | None] = ContextVar("mcp_principal", default=None)

INSTRUCTIONS = (
    "Penny manages this business's Stripe account. Read tools return pre-formatted amounts. "
    "Refunds, invoices and payment links are only PROPOSED by propose_* tools; show the owner the "
    "preview and call confirm_action only after they explicitly approve. Proposals expire after "
    "10 minutes."
)


class ConfirmIn(BaseModel):
    action_id: str = Field(description="The action_id returned by a propose_* tool.")


class DecisionOut(BaseModel):
    action_id: str
    status: str  # executed | failed | expired | cancelled
    preview: str
    stripe_object_id: str | None = None
    error: str | None = None


def _annotations(spec: ToolSpec) -> types.ToolAnnotations:
    if spec.mutating:  # propose_* only records a proposal; nothing moves yet
        return types.ToolAnnotations(read_only_hint=False, destructive_hint=False)
    return types.ToolAnnotations(read_only_hint=True)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def _ok(payload: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
    )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


def build_server(state: AppState) -> Server:
    owner_tools = {t.name: t for t in tools_for("owner")}

    async def list_tools(ctx, params) -> types.ListToolsResult:
        tools = [
            types.Tool(
                name=t.name,
                description=t.description,
                input_schema=t.json_schema()["function"]["parameters"],
                annotations=_annotations(t),
            )
            for t in owner_tools.values()
        ]
        tools += [
            types.Tool(
                name="confirm_action",
                description=(
                    "EXECUTE a proposal (refund, invoice, payment link) in Stripe. Only call this "
                    "after the owner has seen the preview and explicitly said yes."
                ),
                input_schema=_schema(ConfirmIn),
                annotations=types.ToolAnnotations(
                    read_only_hint=False, destructive_hint=True, idempotent_hint=False
                ),
            ),
            types.Tool(
                name="cancel_action",
                description="Cancel a pending proposal so it can't be executed.",
                input_schema=_schema(ConfirmIn),
                annotations=types.ToolAnnotations(read_only_hint=False, destructive_hint=False),
            ),
        ]
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        principal = _principal.get()
        if principal is None:  # the auth wrapper should make this impossible
            return _error("Not authenticated.")
        args = params.arguments or {}
        async with state.sessionmaker() as session, session.begin():
            if params.name in ("confirm_action", "cancel_action"):
                return await _decide(state, session, principal, params.name, args)
            spec = owner_tools.get(params.name)
            if spec is None:
                return _error(f"Unknown tool {params.name!r}.")
            tool_ctx = ToolContext(
                session=session,
                settings=state.settings,
                now=state.clock(),
                owner_id=principal.owner_id,
                api_key_id=principal.key_id,
                owner_gateway=state.owner_gateway(),
            )
            try:
                result = await spec.run(tool_ctx, args)
            except ValidationError as exc:
                return _error(f"Invalid arguments: {exc.errors(include_url=False)}")
            except ToolError as exc:
                return _error(str(exc))
            except Exception:
                logger.exception("mcp tool %s failed", spec.name)
                return _error(f"The {spec.name} lookup failed.")
            return _ok(result.model_dump(mode="json"))

    return Server(
        "penny-payments",
        version="0.2.0",
        title="Penny (payments assistant)",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def _decide(state, session, principal, name: str, args: dict[str, Any]):
    try:
        action_id = UUID(ConfirmIn.model_validate(args).action_id)
    except (ValidationError, ValueError):
        return _error("action_id must be the UUID returned by a propose_* tool.")
    try:
        if name == "confirm_action":
            action = await actions.confirm(
                session,
                state.owner_gateway(),
                action_id=action_id,
                owner_id=principal.owner_id,
                api_key_id=principal.key_id,
                now=state.clock(),
            )
        else:
            action = await actions.cancel(
                session,
                action_id=action_id,
                owner_id=principal.owner_id,
                api_key_id=principal.key_id,
            )
    except actions.ActionError as exc:
        return _error(str(exc))
    out = DecisionOut(
        action_id=str(action.id),
        status=action.status,
        preview=action.preview,
        stripe_object_id=action.stripe_object_id,
        error=action.error,
    )
    result = _ok(out.model_dump())
    result.is_error = action.status in ("failed", "expired")
    return result


class MCPEndpoint:
    """ASGI endpoint for /mcp: API-key auth, then the MCP session manager."""

    def __init__(self, state: AppState):
        self.state = state
        self.manager = StreamableHTTPSessionManager(
            app=build_server(state),
            stateless=True,
            json_response=True,
            # Rebinding protection guards unauthenticated localhost servers; every request here
            # needs a key, and the Next proxy reaches us as `api:8000`.
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        scheme, _, token = headers.get("authorization", "").partition(" ")
        principal = None
        if scheme.lower() == "bearer" and api_keys.is_api_key(token.strip()):
            async with self.state.sessionmaker() as session, session.begin():
                principal = await api_keys.resolve(session, token.strip(), now=self.state.clock())
        if principal is None:
            response = JSONResponse(
                {"error": "invalid_token", "detail": "An owner API key (pak_...) is required."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="penny-mcp"'},
            )
            await response(scope, receive, send)
            return
        reset = _principal.set(principal)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            _principal.reset(reset)
