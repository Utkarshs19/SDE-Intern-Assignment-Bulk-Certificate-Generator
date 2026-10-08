"""Creating a generation job."""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_create_job_returns_202_with_job_details(client, make_payload):
    resp = client.post("/jobs", json=make_payload())

    assert resp.status_code == 202
    body = resp.json()
    assert body["id"]
    assert resp.headers["location"] == f"/jobs/{body['id']}"
    assert body["title"] == "Test job"
    assert body["counts"]["total"] == 3
    assert body["certificate"]["course_name"] == "Advanced Python Workshop"
    assert body["links"]["certificates"] == f"/jobs/{body['id']}/certificates"


def test_create_job_persists_one_row_per_recipient(client, make_payload):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]

    listing = client.get(f"/jobs/{job_id}/certificates").json()
    assert listing["total"] == 3
    assert [i["recipient_name"] for i in listing["items"]] == ["Asha Verma", "Rahul Mehta", "Priya Nair"]
    assert [i["position"] for i in listing["items"]] == [0, 1, 2]


def test_issue_date_defaults_to_today(client, make_payload):
    payload = make_payload()
    del payload["certificate"]["issue_date"]
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 202
    assert resp.json()["certificate"]["issue_date"]


def test_background_mode_returns_before_work_and_then_completes(make_client, make_payload):
    client = make_client(processing_mode="background")
    resp = client.post("/jobs", json=make_payload(names=[f"Person {i}" for i in range(20)]))
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    deadline = time.time() + 15
    body = client.get(f"/jobs/{job_id}").json()
    while body["status"] in ("queued", "processing") and time.time() < deadline:
        time.sleep(0.05)
        body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "completed"
    assert body["counts"]["succeeded"] == 20
    assert body["finished_at"] is not None


def test_unfinished_jobs_are_resumed_on_startup(make_client, make_payload, tmp_path):
    # App #1 accepts the job but never runs it (simulates a crash before processing).
    first = make_client()
    first.app.state.runner.submit = lambda job_id: None
    job_id = first.post("/jobs", json=make_payload()).json()["id"]
    assert first.get(f"/jobs/{job_id}").json()["status"] == "queued"

    # App #2 starts on the same database and picks the job up during startup.
    second = make_client()
    body = second.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["counts"]["succeeded"] == 3


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404
    assert client.get("/jobs/does-not-exist/certificates").status_code == 404
    assert client.get("/jobs/does-not-exist/download").status_code == 404
