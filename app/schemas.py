"""Request / response models."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CertificateInfo(BaseModel):
    """Details printed on every certificate in the job."""

    model_config = ConfigDict(str_strip_whitespace=True)

    course_name: str = Field(min_length=1, max_length=150, examples=["Advanced Python Workshop"])
    issuer: str = Field(min_length=1, max_length=150, examples=["Acme Learning Academy"])
    issue_date: date = Field(default_factory=date.today, examples=["2026-10-01"])
    signatory_name: str | None = Field(default=None, max_length=100, examples=["Dr. Meera Iyer"])
    signatory_title: str | None = Field(default=None, max_length=100, examples=["Program Director"])


class JobCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200, description="Free-text label for the job")
    certificate: CertificateInfo
    # Deliberately loosely typed: each recipient is validated individually so a bad
    # row fails on its own instead of rejecting the whole request.
    recipients: list[Any] = Field(
        min_length=1,
        description="Objects like {'name': 'Asha Verma', 'email': 'asha@example.com', "
        "'achievement': 'with Distinction'}. Only 'name' is required.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "October workshop",
                "certificate": {
                    "course_name": "Advanced Python Workshop",
                    "issuer": "Acme Learning Academy",
                    "issue_date": "2026-10-01",
                    "signatory_name": "Dr. Meera Iyer",
                    "signatory_title": "Program Director",
                },
                "recipients": [
                    {"name": "Asha Verma", "email": "asha@example.com"},
                    {"name": "Rahul Mehta", "achievement": "with Distinction"},
                ],
            }
        }
    )


class JobCounts(BaseModel):
    total: int
    succeeded: int
    failed: int
    pending: int


class FailedRecipient(BaseModel):
    position: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobLinks(BaseModel):
    self: str
    certificates: str
    download_all: str


class JobDetail(BaseModel):
    id: str
    title: str | None
    status: str
    progress_percent: float
    counts: JobCounts
    certificate: CertificateInfo
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failures: list[FailedRecipient]
    failures_truncated: bool
    links: JobLinks


class CertificateItem(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    certificate_number: str
    status: Literal["pending", "succeeded", "failed"]
    error: str | None
    generated_at: datetime | None
    download_url: str | None


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateItem]
