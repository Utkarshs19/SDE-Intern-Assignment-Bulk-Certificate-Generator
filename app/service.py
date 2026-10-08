"""Business logic: creating jobs and processing them."""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Certificate, CertificateStatus, Job, JobStatus, new_id, utcnow
from app.pdf import CertificateData, CertificateGenerationError, ensure_renderable
from app.schemas import JobCreateRequest
from app.storage import LocalStorage
from app.validation import validate_recipient

log = logging.getLogger("certgen")

Generator = Callable[[CertificateData], bytes]
MAX_STORED_ERROR = 500


class InvalidTemplateError(ValueError):
    """The job-level certificate info cannot be rendered (affects every recipient)."""


def _best_effort_str(raw: Any, key: str) -> str | None:
    if isinstance(raw, dict) and isinstance(raw.get(key), str):
        return raw[key].strip()[:200] or None
    return None


def create_job(session: Session, payload: JobCreateRequest, *, font_path: str | None = None) -> Job:
    """Validate every recipient and persist the job plus one row per recipient.

    Invalid recipients are stored as already-failed rows (with the reason); valid
    ones are stored as pending and picked up by the worker.
    """
    info = payload.certificate
    try:
        ensure_renderable(
            [
                ("course_name", info.course_name),
                ("issuer", info.issuer),
                ("signatory_name", info.signatory_name),
                ("signatory_title", info.signatory_title),
            ],
            font_path,
        )
    except CertificateGenerationError as exc:
        raise InvalidTemplateError(str(exc)) from exc

    job = Job(
        id=new_id(),
        title=payload.title,
        status=JobStatus.QUEUED,
        course_name=info.course_name,
        issuer=info.issuer,
        issue_date=info.issue_date,
        signatory_name=info.signatory_name,
        signatory_title=info.signatory_title,
        total_count=len(payload.recipients),
    )

    rows: list[Certificate] = []
    seen_emails: set[str] = set()
    for position, raw in enumerate(payload.recipients):
        recipient, error = validate_recipient(raw)
        if recipient is not None and recipient.email:
            key = recipient.email.lower()
            if key in seen_emails:
                recipient, error = None, "email: duplicate of an earlier recipient in this request"
            else:
                seen_emails.add(key)

        rows.append(
            Certificate(
                id=new_id(),
                job_id=job.id,
                position=position,
                recipient_name=recipient.name if recipient else _best_effort_str(raw, "name"),
                recipient_email=recipient.email if recipient else _best_effort_str(raw, "email"),
                achievement=recipient.achievement if recipient else None,
                raw_data=raw,
                status=CertificateStatus.PENDING if recipient else CertificateStatus.FAILED,
                error=error,
                certificate_number=f"CERT-{job.id[:8].upper()}-{position + 1:05d}",
            )
        )

    if all(r.status == CertificateStatus.FAILED for r in rows):
        job.status = JobStatus.FAILED  # nothing to process
        job.finished_at = utcnow()

    session.add(job)
    session.flush()  # job row must exist before certificates reference it
    session.add_all(rows)
    session.commit()
    return job


def get_counts(session: Session, job_id: str) -> dict[str, int]:
    rows = session.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {CertificateStatus.PENDING: 0, CertificateStatus.SUCCEEDED: 0, CertificateStatus.FAILED: 0}
    counts.update({status: n for status, n in rows})
    return counts


def final_status(counts: dict[str, int]) -> str:
    if counts[CertificateStatus.SUCCEEDED] == 0:
        return JobStatus.FAILED
    if counts[CertificateStatus.FAILED] == 0:
        return JobStatus.COMPLETED
    return JobStatus.COMPLETED_WITH_ERRORS


def _describe(exc: Exception) -> str:
    if isinstance(exc, CertificateGenerationError):
        message = str(exc)
    else:
        message = f"unexpected error ({type(exc).__name__}): {exc}"
    return message[:MAX_STORED_ERROR]


def process_job(
    job_id: str,
    *,
    session_factory: sessionmaker,
    storage: LocalStorage,
    generator: Generator,
) -> None:
    """Generate every pending certificate of a job.

    * Each certificate is generated and committed on its own, so one failure never
      affects the others and progress is visible while the job runs.
    * Only *pending* certificates are touched, which makes the function safe to
      re-run (e.g. after a crash/restart) without redoing finished work.
    """
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in JobStatus.ACTIVE:
            return

        job.status = JobStatus.PROCESSING
        job.started_at = job.started_at or utcnow()
        session.commit()

        pending_ids = session.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.position)
        ).all()

        for cert_id in pending_ids:
            cert = session.get(Certificate, cert_id)
            try:
                data = CertificateData(
                    recipient_name=cert.recipient_name,
                    course_name=job.course_name,
                    issuer=job.issuer,
                    issue_date=job.issue_date,
                    certificate_number=cert.certificate_number,
                    achievement=cert.achievement,
                    signatory_name=job.signatory_name,
                    signatory_title=job.signatory_title,
                )
                pdf_bytes = generator(data)
                cert.file_path = storage.save(job_id, cert.id, pdf_bytes)
                cert.status = CertificateStatus.SUCCEEDED
                cert.error = None
                cert.generated_at = utcnow()
            except Exception as exc:  # isolate per-certificate failures
                log.warning("certificate %s (job %s) failed: %s", cert.certificate_number, job_id, exc)
                cert.status = CertificateStatus.FAILED
                cert.error = _describe(exc)
            session.commit()

        job.status = final_status(get_counts(session, job_id))
        job.finished_at = utcnow()
        session.commit()


def recover_unfinished_jobs(session_factory: sessionmaker, runner) -> int:
    """Re-queue jobs that were queued/processing when the process last stopped."""
    with session_factory() as session:
        job_ids = session.scalars(
            select(Job.id).where(Job.status.in_(JobStatus.ACTIVE)).order_by(Job.created_at)
        ).all()
    for job_id in job_ids:
        runner.submit(job_id)
    return len(job_ids)
