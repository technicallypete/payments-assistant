"""Owner tools: read the whole account, and PROPOSE mutations (never execute them).

Money in outputs is pre-formatted (`format_money`) so the model quotes figures instead of computing
them. Date ranges come from named periods resolved in code (`timeutil`).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from payments_assistant.core.money import format_money
from payments_assistant.core.services import actions, reporting
from payments_assistant.core.services.actions import (
    CreateInvoiceParams,
    CreatePaymentLinkParams,
    RefundParams,
)
from payments_assistant.core.stripe_types import CustomerInfo, StripeObjectNotFound
from payments_assistant.core.timeutil import (
    DueDateError,
    Period,
    local_today,
    period_range,
    previous_range,
    resolve_due_date,
)
from payments_assistant.core.tools.registry import ToolContext, ToolError, tool

# ------------------------------------------------------------------------------- shared outputs


class Money(BaseModel):
    cents: int
    display: str


class ActionProposal(BaseModel):
    """Returned by every mutating tool. Nothing has happened in Stripe yet."""

    action_id: str
    action_type: str
    preview: str
    expires_at: datetime
    note: str = (
        "Proposed only. The owner sees Confirm/Cancel buttons; nothing happens until they confirm."
    )


def _money(ctx: ToolContext, cents: int, currency: str | None = None) -> Money:
    return Money(cents=cents, display=format_money(cents, currency or ctx.currency))


def _local(ctx: ToolContext, dt: datetime) -> str:
    return dt.astimezone(ctx.tz).strftime("%a %b %-d, %Y %-I:%M %p")


async def _match_customers(ctx: ToolContext, query: str) -> list[CustomerInfo]:
    _, gw = ctx.owner()
    matches = await gw.search_customers(query)
    if not matches:
        raise ToolError(f"No customer matches {query!r}. Try find_customers with a shorter name.")
    return matches


async def _record_proposal(ctx: ToolContext, action) -> ActionProposal:
    proposal = ActionProposal(
        action_id=str(action.id),
        action_type=action.action_type,
        preview=action.preview,
        expires_at=action.expires_at,
    )
    ctx.proposals.append(proposal)
    return proposal


# ------------------------------------------------------------------------------- activity


class ActivityIn(BaseModel):
    period: Period = Field(
        Period.TODAY,
        description="Named period, resolved in the business timezone. Weeks run Mon–Sun.",
    )
    compare_to: Period | None = Field(
        None,
        description=(
            "Period to compare against. Omit for the natural previous period "
            "(today→yesterday so far, last_week→week_before_last, ...)."
        ),
    )


class CustomerAmount(BaseModel):
    customer: str
    amount: str


class PeriodSummary(BaseModel):
    period: str
    from_local: str
    to_local: str
    gross: str
    refunded: str
    net: str
    successful_payments: int
    failed_payments: int
    failures_by_reason: dict[str, int]
    average_payment: str
    top_customers: list[CustomerAmount]


class OpenInvoiceOut(BaseModel):
    invoice_id: str
    customer: str
    amount_remaining: str
    due_date: str | None
    overdue: bool


class ActivityOut(BaseModel):
    current: PeriodSummary
    previous: PeriodSummary
    gross_change: str
    gross_change_pct: float | None
    open_invoices: list[OpenInvoiceOut]


async def _period_summary(
    ctx: ToolContext, label: str, start: datetime, end: datetime
) -> tuple[PeriodSummary, reporting.PeriodStats]:
    _, gw = ctx.owner()
    stats = reporting.summarize(
        await gw.list_payments(start=start, end=end), start=start, end=end, currency=ctx.currency
    )
    fm = lambda c: format_money(c, ctx.currency)  # noqa: E731
    return (
        PeriodSummary(
            period=label,
            from_local=_local(ctx, start),
            to_local=_local(ctx, end),
            gross=fm(stats.gross),
            refunded=fm(stats.refunded),
            net=fm(stats.net),
            successful_payments=stats.successful_count,
            failed_payments=stats.failed_count,
            failures_by_reason=stats.failures_by_reason,
            average_payment=fm(stats.average_payment),
            top_customers=[
                CustomerAmount(customer=t.customer, amount=fm(t.amount))
                for t in stats.top_customers
            ],
        ),
        stats,
    )


@tool(
    name="get_activity",
    audience="owner",
    input_model=ActivityIn,
    output_model=ActivityOut,
    label="Pulling payment activity",
)
async def get_activity(ctx: ToolContext, args: ActivityIn) -> ActivityOut:
    """Payment activity for a period (takings, refunds, declines and their reasons, top customers),
    compared with the previous period, plus all currently open invoices. Use for daily summaries
    and questions like "how much did we take last week compared to the week before?"."""
    _, gw = ctx.owner()
    start, end = period_range(args.period, ctx.now, ctx.tz)
    if args.compare_to is None:
        p_start, p_end = previous_range(args.period, ctx.now, ctx.tz)
        p_label = f"previous {args.period.value.replace('_', ' ')}"
    else:
        p_start, p_end = period_range(args.compare_to, ctx.now, ctx.tz)
        p_label = args.compare_to.value
    current, cur_stats = await _period_summary(ctx, args.period.value, start, end)
    previous, prev_stats = await _period_summary(ctx, p_label, p_start, p_end)
    cmp = reporting.compare(cur_stats, prev_stats)
    invoices = reporting.open_invoices(await gw.list_invoices(status="open"), now=ctx.now)
    return ActivityOut(
        current=current,
        previous=previous,
        gross_change=format_money(cmp.gross_change, ctx.currency),
        gross_change_pct=cmp.gross_change_pct,
        open_invoices=[
            OpenInvoiceOut(
                invoice_id=i.invoice_id,
                customer=i.customer,
                amount_remaining=format_money(i.amount_remaining, i.currency),
                due_date=i.due_date.astimezone(ctx.tz).date().isoformat() if i.due_date else None,
                overdue=i.overdue,
            )
            for i in invoices
        ],
    )


# ------------------------------------------------------------------------------- lookups


class FindCustomersIn(BaseModel):
    query: str = Field(min_length=1, description="Part of the customer's name or email.")


class CustomerOut(BaseModel):
    customer_id: str
    name: str
    email: str | None


class FindCustomersOut(BaseModel):
    customers: list[CustomerOut]


@tool(
    name="find_customers",
    audience="owner",
    input_model=FindCustomersIn,
    output_model=FindCustomersOut,
    label="Looking up customers",
)
async def find_customers(ctx: ToolContext, args: FindCustomersIn) -> FindCustomersOut:
    """Find customers by (partial) name or email. Returns their Stripe customer ids."""
    _, gw = ctx.owner()
    return FindCustomersOut(
        customers=[
            CustomerOut(customer_id=c.id, name=c.name, email=c.email)
            for c in await gw.search_customers(args.query)
        ]
    )


class FindPaymentsIn(BaseModel):
    customer_query: str | None = Field(
        None, description="Customer name/email to filter by (e.g. 'Maya'). Omit for everyone."
    )
    period: Period = Field(Period.LAST_30_DAYS, description="How far back to look.")
    status: Literal["succeeded", "failed", "any"] = "any"
    limit: int = Field(10, ge=1, le=50, description="Newest first. Use 1 for 'last payment'.")


class PaymentOut(BaseModel):
    charge_id: str
    customer: str | None
    amount: str
    refunded: str
    refundable: str
    status: str
    decline_reason: str | None
    when: str


class FindPaymentsOut(BaseModel):
    payments: list[PaymentOut]


@tool(
    name="find_payments",
    audience="owner",
    input_model=FindPaymentsIn,
    output_model=FindPaymentsOut,
    label="Looking up payments",
)
async def find_payments(ctx: ToolContext, args: FindPaymentsIn) -> FindPaymentsOut:
    """List individual payments (charges), newest first, optionally for one customer. Use this to
    identify a specific payment before proposing a refund, e.g. "Maya's last payment" →
    customer_query="Maya", status="succeeded", limit=1."""
    _, gw = ctx.owner()
    start, end = period_range(args.period, ctx.now, ctx.tz)
    payments = await gw.list_payments(start=start, end=end)
    if args.customer_query:
        ids = {c.id for c in await _match_customers(ctx, args.customer_query)}
        payments = [p for p in payments if p.customer_id in ids]
    if args.status != "any":
        payments = [p for p in payments if p.status == args.status]
    payments = sorted(payments, key=lambda p: p.occurred_at, reverse=True)[: args.limit]
    return FindPaymentsOut(
        payments=[
            PaymentOut(
                charge_id=p.id,
                customer=p.customer_name,
                amount=format_money(p.amount, p.currency),
                refunded=format_money(p.amount_refunded, p.currency),
                refundable=format_money(
                    p.amount - p.amount_refunded if p.status == "succeeded" else 0, p.currency
                ),
                status=p.status,
                decline_reason=p.decline_code or p.failure_code,
                when=_local(ctx, p.occurred_at),
            )
            for p in payments
        ]
    )


class ListInvoicesIn(BaseModel):
    status: Literal["open", "paid", "void", "uncollectible", "all"] = "open"
    customer_query: str | None = Field(None, description="Customer name/email filter.")


class InvoiceOut(BaseModel):
    invoice_id: str
    number: str | None
    customer: str | None
    status: str
    amount_due: str
    amount_remaining: str
    due_date: str | None


class ListInvoicesOut(BaseModel):
    invoices: list[InvoiceOut]


@tool(
    name="list_invoices",
    audience="owner",
    input_model=ListInvoicesIn,
    output_model=ListInvoicesOut,
    label="Checking invoices",
)
async def list_invoices(ctx: ToolContext, args: ListInvoicesIn) -> ListInvoicesOut:
    """List invoices by status (default: open/unpaid), optionally for one customer."""
    _, gw = ctx.owner()
    if args.customer_query:
        invoices = []
        for c in await _match_customers(ctx, args.customer_query):
            invoices += await gw.list_invoices(
                status=None if args.status == "all" else args.status, customer_id=c.id
            )
    else:
        invoices = await gw.list_invoices(status=None if args.status == "all" else args.status)
    return ListInvoicesOut(
        invoices=[
            InvoiceOut(
                invoice_id=i.id,
                number=i.number,
                customer=i.customer_name,
                status=i.status,
                amount_due=format_money(i.amount_due, i.currency),
                amount_remaining=format_money(i.amount_remaining, i.currency),
                due_date=i.due_date.astimezone(ctx.tz).date().isoformat() if i.due_date else None,
            )
            for i in invoices
        ]
    )


# ------------------------------------------------------------------------------- proposals


class ProposeRefundIn(BaseModel):
    charge_id: str = Field(description="The ch_... id from find_payments.")
    amount_cents: int | None = Field(
        None, gt=0, description="Partial refund in cents. Omit to refund everything refundable."
    )


@tool(
    name="propose_refund",
    audience="owner",
    input_model=ProposeRefundIn,
    output_model=ActionProposal,
    label="Preparing a refund",
    mutating=True,
)
async def propose_refund(ctx: ToolContext, args: ProposeRefundIn) -> ActionProposal:
    """Propose refunding a payment. Does NOT refund: it creates a proposal the owner must confirm.
    Find the charge_id first with find_payments."""
    owner_id, gw = ctx.owner()
    try:
        payment = await gw.get_payment(args.charge_id)
    except StripeObjectNotFound as exc:
        raise ToolError(f"No payment {args.charge_id}. Use find_payments to get the id.") from exc
    refundable = payment.amount - payment.amount_refunded
    if payment.status != "succeeded" or refundable <= 0:
        raise ToolError("That payment has nothing left to refund.")
    amount = args.amount_cents or refundable
    if amount > refundable:
        raise ToolError(
            f"At most {format_money(refundable, payment.currency)} can be refunded on that payment."
        )
    kind = "Refund" if amount == payment.amount else "Partial refund of"
    preview = (
        f"{kind} {format_money(amount, payment.currency)} to "
        f"{payment.customer_name or 'customer'} (payment of "
        f"{format_money(payment.amount, payment.currency)} on {_local(ctx, payment.occurred_at)})"
    )
    action = await actions.propose(
        ctx.session,
        owner_id=owner_id,
        action_type="refund",
        params=RefundParams(charge_id=payment.id, amount=None if amount == refundable else amount),
        preview=preview,
        ttl_minutes=ctx.settings.action_proposal_ttl_minutes,
        conversation_id=ctx.conversation_id,
        api_key_id=ctx.api_key_id,
        now=ctx.now,
    )
    return await _record_proposal(ctx, action)


class ProposeInvoiceIn(BaseModel):
    customer_id: str = Field(description="The cus_... id from find_customers.")
    amount_cents: int = Field(gt=0, description="Invoice total in cents, e.g. 25000 for $250.")
    description: str = Field(
        "Services",
        min_length=1,
        max_length=200,
        description="Line item text. Defaults to 'Services' if the owner didn't say.",
    )
    due: str = Field(
        description=(
            "Due date exactly as the owner said it: 'next Friday', 'in 2 weeks', 'end of month', "
            "or YYYY-MM-DD. It is resolved in code."
        )
    )


@tool(
    name="propose_invoice",
    audience="owner",
    input_model=ProposeInvoiceIn,
    output_model=ActionProposal,
    label="Drafting an invoice",
    mutating=True,
)
async def propose_invoice(ctx: ToolContext, args: ProposeInvoiceIn) -> ActionProposal:
    """Propose creating (and finalizing) an invoice for a customer. Does NOT create it: the owner
    must confirm. Look up the customer_id first with find_customers."""
    owner_id, gw = ctx.owner()
    customer = next((c for c in await gw.list_customers() if c.id == args.customer_id), None)
    if customer is None:
        raise ToolError(f"No customer {args.customer_id}. Use find_customers to get the id.")
    today = local_today(ctx.now, ctx.tz)
    try:
        due = resolve_due_date(args.due, today)
    except DueDateError as exc:
        raise ToolError(str(exc)) from exc
    if due < today:
        raise ToolError("The due date is in the past.")
    preview = (
        f"Invoice {customer.name} {format_money(args.amount_cents, ctx.currency)} for "
        f"“{args.description}”, due {due.strftime('%a %b %-d, %Y')}"
    )
    action = await actions.propose(
        ctx.session,
        owner_id=owner_id,
        action_type="create_invoice",
        params=CreateInvoiceParams(
            customer_id=customer.id,
            amount=args.amount_cents,
            currency=ctx.currency,
            description=args.description,
            due_date=due,
        ),
        preview=preview,
        ttl_minutes=ctx.settings.action_proposal_ttl_minutes,
        conversation_id=ctx.conversation_id,
        api_key_id=ctx.api_key_id,
        now=ctx.now,
    )
    return await _record_proposal(ctx, action)


class ProposePaymentLinkIn(BaseModel):
    amount_cents: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=200, description="What the payment is for.")


@tool(
    name="propose_payment_link",
    audience="owner",
    input_model=ProposePaymentLinkIn,
    output_model=ActionProposal,
    label="Preparing a payment link",
    mutating=True,
)
async def propose_payment_link(ctx: ToolContext, args: ProposePaymentLinkIn) -> ActionProposal:
    """Propose creating a shareable Stripe payment link for a fixed amount.
    The owner must confirm."""
    owner_id, _ = ctx.owner()
    preview = (
        f"Payment link for {format_money(args.amount_cents, ctx.currency)} (“{args.description}”)"
    )
    action = await actions.propose(
        ctx.session,
        owner_id=owner_id,
        action_type="create_payment_link",
        params=CreatePaymentLinkParams(
            amount=args.amount_cents, currency=ctx.currency, description=args.description
        ),
        preview=preview,
        ttl_minutes=ctx.settings.action_proposal_ttl_minutes,
        conversation_id=ctx.conversation_id,
        api_key_id=ctx.api_key_id,
        now=ctx.now,
    )
    return await _record_proposal(ctx, action)
