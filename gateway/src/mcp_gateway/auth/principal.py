"""The authenticated caller, as established from a validated JWT."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, StringConstraints

# Tenant and session ids become parts of Redis keys and hash tags ("{tenant/sid}") and of
# database rows, so they are restricted to characters that cannot break either.
TENANT_ID_REGEX: Final = r"^[a-z0-9][a-z0-9_-]{0,62}$"
SESSION_ID_REGEX: Final = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
SUBJECT_REGEX: Final = r"^[^\s{}]{1,256}$"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: Annotated[str, StringConstraints(pattern=SUBJECT_REGEX)]
    """JWT ``sub``: the agent or human identity."""
    tenant_id: Annotated[str, StringConstraints(pattern=TENANT_ID_REGEX)]
    role: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    """JWT ``role``: matched against ``roles`` in policies.yaml (unknown roles get nothing)."""
    session_id: Annotated[str, StringConstraints(pattern=SESSION_ID_REGEX)] | None = None
    """JWT ``sid``: the agent session that budgets and loop detection are keyed on.
    Required on the MCP endpoint; operator tokens may omit it."""
    token_id: str | None = None
    expires_at: datetime
