"""Registry invariants that keep the privacy and confirm-gate guarantees structural."""

import re

import pytest
from pydantic import BaseModel

from payments_assistant.core.tools import REGISTRY, tools_for
from payments_assistant.core.tools.owner import ActionProposal

# Any input field that could let a customer tool address someone else.
FORBIDDEN = re.compile(r"customer|account|email|stripe|cus_|user|owner|phone|name", re.I)


def _field_names(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


def test_registry_has_both_audiences():
    assert tools_for("owner")
    assert tools_for("customer")


@pytest.mark.parametrize("spec", tools_for("customer"), ids=lambda s: s.name)
def test_customer_tools_have_no_identifying_inputs(spec):
    offending = {f for f in _field_names(spec.input_model) if FORBIDDEN.search(f)}
    assert not offending, f"{spec.name} exposes {offending}"
    # Also scan the JSON schema the model actually sees (nested models, descriptions aside).
    props = spec.json_schema()["function"]["parameters"].get("properties", {})
    assert not {p for p in props if FORBIDDEN.search(p)}


@pytest.mark.parametrize("spec", tools_for("customer"), ids=lambda s: s.name)
def test_customer_tools_never_mutate_stripe(spec):
    assert not spec.mutating


@pytest.mark.parametrize("spec", [s for s in REGISTRY.values() if s.mutating], ids=lambda s: s.name)
def test_mutating_tools_return_proposals(spec):
    assert spec.audience == "owner"
    assert spec.output_model is ActionProposal
    assert spec.name.startswith("propose_")


@pytest.mark.parametrize("spec", list(REGISTRY.values()), ids=lambda s: s.name)
def test_every_tool_is_described(spec):
    schema = spec.json_schema()["function"]
    assert schema["name"] == spec.name
    assert len(schema["description"]) > 20
    assert spec.label
    assert schema["parameters"]["type"] == "object"


def test_owner_tool_set():
    assert {t.name for t in tools_for("owner")} >= {
        "get_activity",
        "find_customers",
        "find_payments",
        "list_invoices",
        "propose_refund",
        "propose_invoice",
        "propose_payment_link",
    }
