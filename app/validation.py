"""Per-recipient validation.

Recipients are validated one by one (not as part of the request schema) so that
a single bad row is reported against that row instead of rejecting the whole
request.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class Recipient(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    # Optional per-recipient line, e.g. "with Distinction" or "Grade A".
    achievement: str | None = Field(default=None, max_length=100)

    @field_validator("name", "achievement")
    @classmethod
    def _no_control_chars(cls, value: str | None) -> str | None:
        if value is not None and _CONTROL_CHARS_RE.search(value):
            raise ValueError("must not contain control characters or line breaks")
        return value or None

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not _EMAIL_RE.match(value):
            raise ValueError("is not a valid email address")
        return value


def validate_recipient(raw: Any) -> tuple[Recipient | None, str | None]:
    """Return (recipient, None) if valid, otherwise (None, human readable error)."""
    if not isinstance(raw, dict):
        return None, "recipient must be a JSON object with at least a 'name'"
    try:
        return Recipient.model_validate(raw), None
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "recipient"
            parts.append(f"{field}: {err['msg']}")
        return None, "; ".join(parts)
