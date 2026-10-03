"""Customer tools (Telegram bot). The bound customer always comes from `ctx.customer()`.

No input model here may carry a customer-identifying field: the model cannot even express
"another customer". Outputs are built only from the customer gateway, which filters and re-checks
ownership, and DB writes happen inside the caller's `customer_scope` (RLS).
"""

from datetime import date

from pydantic import BaseModel, Field

from payments_assistant.core.money import format_money
from payments_assistant.core.services import payments
from payments_assistant.core.tools.registry import ToolContext, ToolError, tool

# ---------------------------------------------------------------------------- inputs


class NoArgs(BaseModel):
    pass


class ListInvoicesInput(BaseModel):
    include_paid: bool = Field(False, description="Also include invoices already paid or voided.")


class ListPaymentsInput(BaseModel):
    limit: int = Field(5, ge=1, le=20, description="How many recent payments to show.")


class PayInvoiceInput(BaseModel):
    invoice_ref: str | None = Field(
        None,
        description=(
            "Which invoice: its id (in_...) or its number (e.g. MAYA-0007), exactly as shown by "
            "list_my_invoices or get_my_balance. Omit it when the customer has only one open "
            "invoice (e.g. they just said 'pay it'). Never invent one."
        ),
    )


class RequestHumanInput(BaseModel):
    summary: str = Field(
        min_length=1,
        max_length=1000,
        description="One or two sentences for the team: what the customer needs and why.",
    )


# ---------------------------------------------------------------------------- outputs


class MyInvoice(BaseModel):
    invoice_id: str
    number: str | None
    status: str
    amount_remaining: str
    due_date: date | None
    description: str | None


class BalanceOutput(BaseModel):
    total_owed: str
    open_invoice_count: int
    open_invoices: list[MyInvoice]


class InvoicesOutput(BaseModel):
    invoices: list[MyInvoice]


class MyPayment(BaseModel):
    amount: str
    status: str
    date: date
    description: str | None
    decline_reason: str | None


class PaymentsOutput(BaseModel):
    payments: list[MyPayment]


class PayInvoiceOutput(BaseModel):
    kind: str  # payment_link | handoff | already_paid | not_payable
    amount: str | None
    url: str | None
    message_for_customer: str


class RequestHumanOutput(BaseModel):
    message_for_customer: str


# ---------------------------------------------------------------------------- helpers


def _invoice(ctx: ToolContext, inv) -> MyInvoice:
    return MyInvoice(
        invoice_id=inv.id,
        number=inv.number,
        status=inv.status,
        amount_remaining=format_money(inv.amount_remaining, inv.currency),
        due_date=inv.due_date.astimezone(ctx.tz).date() if inv.due_date else None,
        description=inv.description,
    )


# ---------------------------------------------------------------------------- tools


@tool(
    name="get_my_balance",
    audience="customer",
    input_model=NoArgs,
    output_model=BalanceOutput,
    label="Checking your balance",
)
async def get_my_balance(ctx: ToolContext, args: NoArgs) -> BalanceOutput:
    """How much this customer currently owes: total across their open invoices, plus each open
    invoice (id, number, amount, due date). Use for "what do I owe?" or "do I have a balance?"."""
    _, gateway = ctx.customer()
    open_invoices = [i for i in await gateway.list_invoices(open_only=True) if i.amount_remaining]
    currency = open_invoices[0].currency if open_invoices else ctx.currency
    total = sum(i.amount_remaining for i in open_invoices if i.currency == currency)
    return BalanceOutput(
        total_owed=format_money(total, currency),
        open_invoice_count=len(open_invoices),
        open_invoices=[_invoice(ctx, i) for i in open_invoices],
    )


@tool(
    name="list_my_invoices",
    audience="customer",
    input_model=ListInvoicesInput,
    output_model=InvoicesOutput,
    label="Looking up your invoices",
)
async def list_my_invoices(ctx: ToolContext, args: ListInvoicesInput) -> InvoicesOutput:
    """List this customer's invoices. Open invoices only by default; set include_paid to also see
    paid or voided ones."""
    _, gateway = ctx.customer()
    invoices = await gateway.list_invoices(open_only=not args.include_paid)
    return InvoicesOutput(invoices=[_invoice(ctx, i) for i in invoices])


@tool(
    name="list_my_payments",
    audience="customer",
    input_model=ListPaymentsInput,
    output_model=PaymentsOutput,
    label="Looking up your payments",
)
async def list_my_payments(ctx: ToolContext, args: ListPaymentsInput) -> PaymentsOutput:
    """This customer's most recent card payments (newest first), including declined attempts and
    the decline reason."""
    _, gateway = ctx.customer()
    records = await gateway.list_payments(limit=args.limit)
    return PaymentsOutput(
        payments=[
            MyPayment(
                amount=format_money(p.amount, p.currency),
                status=p.status,
                date=p.occurred_at.astimezone(ctx.tz).date(),
                description=p.description,
                decline_reason=(p.decline_code or p.failure_code) if p.status == "failed" else None,
            )
            for p in records
        ]
    )


async def _resolve_invoice_ref(gateway, ref: str | None) -> str:
    """Map an id, an invoice number, or nothing (= the only open invoice) to an invoice id.
    Unknown references pass through unchanged, so they get the generic not-payable answer."""
    if ref and ref.startswith("in_"):
        return ref
    open_invoices = await gateway.list_invoices(open_only=True)
    if ref is None:
        if len(open_invoices) == 1:
            return open_invoices[0].id
        if not open_invoices:
            raise ToolError("You don't have any open invoices right now.")
        raise ToolError(
            "There's more than one open invoice. Ask which one (by number) and call again."
        )
    wanted = ref.strip().lower()
    for inv in await gateway.list_invoices():
        if (inv.number or "").lower() == wanted:
            return inv.id
    return ref


@tool(
    name="pay_invoice",
    audience="customer",
    input_model=PayInvoiceInput,
    output_model=PayInvoiceOutput,
    label="Preparing your payment",
)
async def pay_invoice(ctx: ToolContext, args: PayInvoiceInput) -> PayInvoiceOutput:
    """Start paying one of this customer's open invoices. Returns a secure Stripe payment link
    for the customer to open, or, for large amounts, hands the payment to the team instead.
    Relay `message_for_customer` (and the url, if any) to the customer. Never collect card details
    in chat."""
    account_id, gateway = ctx.customer()
    invoice_id = await _resolve_invoice_ref(gateway, args.invoice_ref)
    outcome = await payments.request_customer_payment(
        ctx.session,
        gateway,
        customer_account_id=account_id,
        invoice_id=invoice_id,
        conversation_id=ctx.conversation_id,
        threshold_cents=ctx.settings.handoff_threshold_cents,
    )
    amount = format_money(outcome.amount, outcome.currency) if outcome.amount else None
    match outcome.kind:
        case "payment_link":
            message = (
                f"Here's your secure payment link for {amount}. It opens Stripe's checkout page, "
                "so your card details never pass through this chat."
            )
        case "handoff":
            message = (
                f"This one is {amount}, which is above what I can take here, so I've passed it "
                "to the team. They'll be in touch to arrange payment."
            )
        case "already_paid":
            message = "That invoice is already paid. Nothing more to do!"
        case _:
            message = "I can't find an open invoice with that reference on your account."
    return PayInvoiceOutput(
        kind=outcome.kind,
        amount=amount if outcome.kind in ("payment_link", "handoff") else None,
        url=outcome.url if outcome.kind == "payment_link" else None,
        message_for_customer=message,
    )


@tool(
    name="request_human",
    audience="customer",
    input_model=RequestHumanInput,
    output_model=RequestHumanOutput,
    label="Passing this to the team",
)
async def request_human(ctx: ToolContext, args: RequestHumanInput) -> RequestHumanOutput:
    """Hand the conversation to a person on the team: use when the customer asks for a human,
    disputes a charge, or needs something these tools can't do."""
    account_id, _ = ctx.customer()
    await payments.request_human(
        ctx.session,
        customer_account_id=account_id,
        conversation_id=ctx.conversation_id,
        summary=args.summary,
    )
    return RequestHumanOutput(
        message_for_customer="I've passed this to the team. Someone will get back to you soon."
    )
