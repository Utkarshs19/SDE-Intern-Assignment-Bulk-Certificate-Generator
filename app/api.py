from __future__ import annotations

import re
import tempfile
import zipfile
from typing import Iterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import service
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import (
    CertificateInfo,
    CertificateItem,
    CertificateList,
    FailedRecipient,
    JobCounts,
    JobCreateRequest,
    JobDetail,
    JobLinks,
)

router = APIRouter()

MAX_FAILURES_IN_JOB_DETAIL = 100


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def _get_job_or_404(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _get_certificate_or_404(session: Session, job_id: str, certificate_id: str) -> Certificate:
    cert = session.get(Certificate, certificate_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(404, "Certificate not found")
    return cert


def _job_detail(session: Session, job: Job) -> JobDetail:
    counts = service.get_counts(session, job.id)
    done = counts[CertificateStatus.SUCCEEDED] + counts[CertificateStatus.FAILED]
    failed_rows = session.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.FAILED)
        .order_by(Certificate.position)
        .limit(MAX_FAILURES_IN_JOB_DETAIL + 1)
    ).all()
    return JobDetail(
        id=job.id,
        title=job.title,
        status=job.status,
        progress_percent=round(100 * done / job.total_count, 1) if job.total_count else 100.0,
        counts=JobCounts(
            total=job.total_count,
            succeeded=counts[CertificateStatus.SUCCEEDED],
            failed=counts[CertificateStatus.FAILED],
            pending=counts[CertificateStatus.PENDING],
        ),
        certificate=CertificateInfo(
            course_name=job.course_name,
            issuer=job.issuer,
            issue_date=job.issue_date,
            signatory_name=job.signatory_name,
            signatory_title=job.signatory_title,
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        failures=[
            FailedRecipient(
                position=c.position,
                recipient_name=c.recipient_name,
                recipient_email=c.recipient_email,
                error=c.error,
            )
            for c in failed_rows[:MAX_FAILURES_IN_JOB_DETAIL]
        ],
        failures_truncated=len(failed_rows) > MAX_FAILURES_IN_JOB_DETAIL,
        links=JobLinks(
            self=f"/jobs/{job.id}",
            certificates=f"/jobs/{job.id}/certificates",
            download_all=f"/jobs/{job.id}/download",
        ),
    )


def _item(cert: Certificate) -> CertificateItem:
    return CertificateItem(
        id=cert.id,
        position=cert.position,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        certificate_number=cert.certificate_number,
        status=cert.status,
        error=cert.error,
        generated_at=cert.generated_at,
        download_url=(
            f"/jobs/{cert.job_id}/certificates/{cert.id}/download"
            if cert.status == CertificateStatus.SUCCEEDED
            else None
        ),
    )


def _file_name(cert: Certificate) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", cert.recipient_name or "").strip("-")[:50] or "recipient"
    return f"{cert.certificate_number}_{slug}.pdf"


@router.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@router.post("/jobs", response_model=JobDetail, status_code=202, tags=["jobs"])
def create_job(
    payload: JobCreateRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> JobDetail:
    """Submit a bulk certificate request.

    Returns **202 Accepted** with the job. Generation continues in the background;
    poll `GET /jobs/{id}` until `status` is `completed`, `completed_with_errors`
    or `failed`.
    """
    state = request.app.state
    if len(payload.recipients) > state.settings.max_recipients_per_job:
        raise HTTPException(
            413,
            f"Too many recipients ({len(payload.recipients)}); "
            f"the limit is {state.settings.max_recipients_per_job} per job. Split into several jobs.",
        )
    try:
        job = service.create_job(session, payload, font_path=state.settings.font_path)
    except service.InvalidTemplateError as exc:
        raise HTTPException(422, str(exc)) from exc

    if job.status == JobStatus.QUEUED:
        state.runner.submit(job.id)
        session.refresh(job)  # the runner may already have progressed it

    response.headers["Location"] = f"/jobs/{job.id}"
    return _job_detail(session, job)


@router.get("/jobs/{job_id}", response_model=JobDetail, tags=["jobs"])
def get_job(job_id: str, session: Session = Depends(get_session)) -> JobDetail:
    """Status, progress and a summary of failures for a job."""
    return _job_detail(session, _get_job_or_404(session, job_id))


@router.get("/jobs/{job_id}/certificates", response_model=CertificateList, tags=["certificates"])
def list_certificates(
    job_id: str,
    status: Literal["pending", "succeeded", "failed"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> CertificateList:
    """Per-recipient results, optionally filtered by status, in request order."""
    _get_job_or_404(session, job_id)
    where = [Certificate.job_id == job_id]
    if status:
        where.append(Certificate.status == status)
    total = session.scalar(select(func.count()).select_from(Certificate).where(*where)) or 0
    rows = session.scalars(
        select(Certificate).where(*where).order_by(Certificate.position).limit(limit).offset(offset)
    ).all()
    return CertificateList(total=total, limit=limit, offset=offset, items=[_item(c) for c in rows])


@router.get("/jobs/{job_id}/certificates/{certificate_id}/download", tags=["certificates"])
def download_certificate(
    job_id: str, certificate_id: str, request: Request, session: Session = Depends(get_session)
):
    """Download one certificate as a PDF."""
    _get_job_or_404(session, job_id)
    cert = _get_certificate_or_404(session, job_id, certificate_id)
    if cert.status == CertificateStatus.PENDING:
        raise HTTPException(409, "Certificate has not been generated yet")
    if cert.status == CertificateStatus.FAILED:
        raise HTTPException(409, f"Certificate generation failed: {cert.error}")
    storage = request.app.state.storage
    if not cert.file_path or not storage.exists(cert.file_path):
        raise HTTPException(410, "Certificate file is no longer available")
    return FileResponse(storage.path(cert.file_path), media_type="application/pdf", filename=_file_name(cert))


@router.get("/jobs/{job_id}/download", tags=["certificates"])
def download_all(job_id: str, request: Request, session: Session = Depends(get_session)):
    """Download every certificate generated so far as one ZIP archive."""
    _get_job_or_404(session, job_id)
    certs = session.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(409, "No certificates have been generated for this job yet")

    storage = request.app.state.storage
    tmp = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            if cert.file_path and storage.exists(cert.file_path):
                zf.write(storage.path(cert.file_path), arcname=_file_name(cert))
    size = tmp.tell()
    tmp.seek(0)

    def stream() -> Iterator[bytes]:
        try:
            while chunk := tmp.read(64 * 1024):
                yield chunk
        finally:
            tmp.close()

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="certificates-{job_id[:8]}.zip"',
            "Content-Length": str(size),
        },
    )
