# Bulk Certificate Generator

A backend API that takes a list of recipients in **one request**, generates a PDF
certificate for each valid recipient from a single predefined template, and lets the
client track progress and download the results.

* **Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default, any SQL database works), ReportLab (PDF)
* **Processing:** background worker pool; job state lives in the database

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # use requirements.txt if you don't need the tests
```

## Run the application

```bash
uvicorn app.main:create_app --factory --reload
```

* API: http://127.0.0.1:8000  
* Interactive docs (try every endpoint in the browser): http://127.0.0.1:8000/docs

Data is written to `./data/` (SQLite file + generated PDFs). Tables are created automatically on startup.

### Configuration (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `CERTGEN_DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host/db` (install the driver) |
| `CERTGEN_STORAGE_DIR` | `./data/certificates` | Where PDFs are stored |
| `CERTGEN_PROCESSING_MODE` | `background` | `background` or `inline` (run inside the request) |
| `CERTGEN_WORKER_THREADS` | `2` | Jobs processed concurrently |
| `CERTGEN_MAX_RECIPIENTS` | `5000` | Max recipients per request (413 above this) |
| `CERTGEN_FONT_PATH` | – | Path to a Unicode `.ttf` for non-Latin names (see *Limitations*) |

## Run the tests

```bash
pytest
```

58 tests, ~2 seconds. Each test gets its own temporary SQLite database and storage folder.

| File | Covers |
|---|---|
| `test_jobs_api.py` | creating a job, background mode, resuming unfinished jobs on startup, 404s |
| `test_validation.py` | request-level validation (422/413) and per-recipient validation |
| `test_generation.py` | PDF content, long names, unsupported characters, files written to disk |
| `test_progress.py` | queued → processing → done, live progress, idempotent re-processing |
| `test_failure_handling.py` | one failure doesn't affect others, all-failed job, failure reporting |
| `test_retrieval.py` | single PDF download, ZIP download, listing/filtering/pagination, 404/409/410 |

## Using the API

### 1. Submit a request

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

```json
{
  "title": "October Python workshop",
  "certificate": {
    "course_name": "Advanced Python Workshop",
    "issuer": "Acme Learning Academy",
    "issue_date": "2026-10-01",
    "signatory_name": "Dr. Meera Iyer",
    "signatory_title": "Program Director"
  },
  "recipients": [
    { "name": "Asha Verma", "email": "asha@example.com" },
    { "name": "Rahul Mehta", "achievement": "with Distinction" }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `certificate.course_name`, `certificate.issuer` | yes | max 150 chars |
| `certificate.issue_date` | no | `YYYY-MM-DD`, defaults to today |
| `certificate.signatory_name` / `signatory_title` | no | signature block, hidden if omitted |
| `recipients[].name` | yes | 1–100 chars, no line breaks |
| `recipients[].email` | no | must look like an email; duplicates within a request are rejected |
| `recipients[].achievement` | no | extra line such as "with Distinction", max 100 chars |

The response is **`202 Accepted`** with the job (including its `id`) and a `Location` header.

### 2. Check progress

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

```json
{
  "id": "6f1c…",
  "status": "completed_with_errors",
  "progress_percent": 100.0,
  "counts": { "total": 4, "succeeded": 3, "failed": 1, "pending": 0 },
  "failures": [
    { "position": 2, "recipient_name": null, "recipient_email": "invalid-row@example.com",
      "error": "name: String should have at least 1 character" }
  ],
  "failures_truncated": false,
  "links": { "self": "/jobs/6f1c…", "certificates": "/jobs/6f1c…/certificates", "download_all": "/jobs/6f1c…/download" },
  "...": "..."
}
```

Job `status` values: `queued` → `processing` → one of

* `completed` – every certificate was generated
* `completed_with_errors` – some succeeded, some failed
* `failed` – nothing could be generated

`position` is the 0-based index of the recipient in the submitted list.

### 3. Retrieve certificates

```bash
# Per-recipient results (filter with ?status=pending|succeeded|failed, paginate with ?limit=&offset=)
curl "http://127.0.0.1:8000/jobs/<job_id>/certificates?status=succeeded&limit=100&offset=0"

# One certificate as PDF (use the download_url from the listing)
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/certificates/<certificate_id>/download

# Everything generated so far as a ZIP
curl -OJ http://127.0.0.1:8000/jobs/<job_id>/download
```

| Endpoint | Success | Errors |
|---|---|---|
| `POST /jobs` | 202 | 422 malformed request, 413 too many recipients |
| `GET /jobs/{id}` | 200 | 404 |
| `GET /jobs/{id}/certificates` | 200 | 404, 422 bad query params |
| `GET /jobs/{id}/certificates/{cid}/download` | 200 PDF | 404; 409 not generated yet / failed (message says why); 410 file missing on disk |
| `GET /jobs/{id}/download` | 200 ZIP | 404; 409 nothing generated yet |

## Design decisions

**Background processing (not synchronous).** A request may contain thousands of recipients, and a
synchronous API would hold the HTTP connection open for the whole run, hit client/proxy timeouts, and
lose all work if the connection drops. Instead `POST /jobs` validates, stores the job and returns `202`
immediately; a `ThreadPoolExecutor` generates the PDFs and the client polls `GET /jobs/{id}`.
I chose an in-process thread pool over Celery/RQ because it needs no extra infrastructure (broker, worker
process) and is plenty for this workload (1,000 certificates ≈ 4 s on a laptop). The processing function
(`service.process_job(job_id)`) only needs a job id, so moving it to a real queue is a small change in
`runner.py`. `inline` mode runs the same code inside the request and is used by most tests so they're deterministic.

**The database is the source of truth.** Every recipient gets a row (`certificates` table) with a status of
`pending`, `succeeded` or `failed`; job counts and progress are computed from those rows rather than from
in-memory state. That gives accurate progress, survives restarts, and lets the same data power the status
and listing endpoints.

**Validation: reject the request vs. fail the row.**
* Problems with the *request as a whole* (missing certificate info, empty list, too many recipients,
  unrenderable template text) are rejected up front with 4xx and nothing is stored.
* Problems with *one recipient* (blank name, bad email, wrong type, duplicate email) don't reject the
  request. The row is stored as `failed` with a readable reason and its position; the valid recipients
  still go ahead. Recipients are therefore typed loosely in the request schema and validated individually
  in `validation.py`.

**Failure isolation.** Each certificate is generated inside its own `try/except` and committed on its own.
Any exception marks only that certificate `failed` (error stored) and the loop moves on. Committing per
certificate also makes progress visible live. A job is `completed`, `completed_with_errors` or `failed`
depending on the final counts.

**Crash/restart safety.** Workers only touch `pending` certificates, so re-running a job is idempotent.
On startup the app re-queues jobs still `queued`/`processing` and they continue where they stopped
(covered by a test).

**Storage.** PDFs are written atomically (temp file + rename) to `data/certificates/<job_id>/<certificate_id>.pdf`;
the DB stores only a relative key. `storage.py` is the single place that touches the filesystem, so it can be
replaced by S3/GCS. Download paths come from the DB, never from user input.

**Template.** One landscape A4 design drawn in code with ReportLab (no external assets). Long names shrink to
fit, long course names wrap onto two lines, and each certificate has a unique, traceable number
(`CERT-<job>-<position>`).

**Small extras:** certificate numbers, ZIP download of a whole job, filtering/pagination on the listing,
duplicate-email detection, a per-request recipient limit, `Location` header, atomic file writes.

## Limitations / what I'd do next

* **Non-Latin names.** The built-in PDF fonts only cover Western European characters, so e.g. a Devanagari
  name fails *that one certificate* with a clear error. To support it, set `CERTGEN_FONT_PATH` to a Unicode
  TTF (such as Noto Sans Devanagari); that font is then used for the whole certificate. (Complex-script
  shaping may need extra work.)
* **Run a single server process.** Startup recovery and the in-process pool assume one process. Multiple
  `uvicorn --workers` would each try to resume the same jobs. Scaling out means a real task queue with job
  claiming (e.g. Celery/RQ + `SELECT … FOR UPDATE SKIP LOCKED`) and a shared object store.
* **No auth, rate limiting, or retention policy.** Anyone who knows a job id can read it (ids are random
  UUIDs). A real deployment needs authentication and job/file cleanup.
* **No migrations.** Tables are created with `create_all`; use Alembic once the schema needs to change.
* **Email format check is a light regex**, not full RFC validation or deliverability checking. No emails are sent.
* **Retry of failed certificates** isn't exposed as an endpoint; the pending-only processing makes it an easy addition.

## Project layout

```
app/
  main.py        app factory, wiring, startup recovery
  api.py         HTTP endpoints
  service.py     create_job (validation + persistence), process_job (generation loop)
  validation.py  per-recipient validation
  pdf.py         the certificate template / renderer
  storage.py     local file storage
  runner.py      inline and thread-pool job runners
  models.py      SQLAlchemy models (Job, Certificate)
  schemas.py     request/response models
  config.py      settings / env vars
tests/           pytest suite
sample_request.json
```
