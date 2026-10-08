from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.database import Base


class JobStatus:
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"  # every certificate succeeded
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some succeeded, some failed
    FAILED = "failed"  # nothing could be generated

    ACTIVE = (QUEUED, PROCESSING)


class CertificateStatus:
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Always hands back timezone-aware UTC datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), index=True, default=JobStatus.QUEUED)

    # The "template variables" shared by every certificate in the job.
    course_name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[date] = mapped_column(Date)
    signatory_name: Mapped[str | None] = mapped_column(String(100))
    signatory_title: Mapped[str | None] = mapped_column(String(100))

    total_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Certificate(Base):
    """One row per recipient in the request, valid or not."""

    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_certificate_job_position"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # 0-based index of the recipient in the submitted list, so clients can map
    # results (especially failures) back to their input.
    position: Mapped[int] = mapped_column(Integer)

    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_email: Mapped[str | None] = mapped_column(String(254))
    achievement: Mapped[str | None] = mapped_column(String(100))
    raw_data: Mapped[dict | list | str | None] = mapped_column(JSON)  # as submitted

    status: Mapped[str] = mapped_column(String(16), default=CertificateStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text)
    certificate_number: Mapped[str] = mapped_column(String(40), unique=True)
    file_path: Mapped[str | None] = mapped_column(String(300))  # key inside storage
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
