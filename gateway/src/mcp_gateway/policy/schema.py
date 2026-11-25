"""JSON Schema export of :class:`PolicyDocument`, for editor validation and completion.

The JSON Schema covers the *shape* of ``policies.yaml``. Cross-section invariants (role
inheritance, approver roles, grant patterns, timing rules) are enforced only by the
Pydantic models, which remain the authority; ``mcp-gateway-policy validate`` runs both.
"""

from __future__ import annotations

import json
from typing import Any

from .models import PolicyDocument

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def policy_json_schema() -> dict[str, Any]:
    schema = PolicyDocument.model_json_schema(mode="validation")
    schema["title"] = "MCP Enterprise Gateway policy (policies.yaml)"
    return {"$schema": JSON_SCHEMA_DIALECT, **schema}


def render_policy_json_schema() -> str:
    """Deterministic, committed form of the schema (``config/policies.schema.json``)."""
    return json.dumps(policy_json_schema(), indent=2, ensure_ascii=False) + "\n"
