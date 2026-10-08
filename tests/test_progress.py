"""Job status / progress."""
from app.pdf import render_certificate


class NoopRunner:
    def submit(self, job_id):
        pass

    def shutdown(self):
        pass


def test_status_before_processing_starts(client, make_payload):
    client.app.state.runner = NoopRunner()
    job = client.post("/jobs", json=make_payload()).json()

    assert job["status"] == "queued"
    assert job["progress_percent"] == 0.0
    assert job["counts"] == {"total": 3, "succeeded": 0, "failed": 0, "pending": 3}
    assert job["started_at"] is None and job["finished_at"] is None

    # Pending certificates are listed but cannot be downloaded yet.
    item = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]
    assert item["status"] == "pending" and item["download_url"] is None
    assert client.get(f"/jobs/{job['id']}/download").status_code == 409


def test_progress_is_visible_while_the_job_is_running(make_client, make_payload):
    snapshots = []
    holder = {}

    def spying_generator(data):
        # Look at the database from the "outside" while the job is mid-flight.
        from app.service import get_counts
        from app.models import Job

        with holder["client"].app.state.session_factory() as session:
            job = session.query(Job).one()
            counts = get_counts(session, job.id)
            snapshots.append((job.status, counts["succeeded"], counts["pending"]))
        return render_certificate(data)

    client = make_client(generator=spying_generator)
    holder["client"] = client
    job = client.post("/jobs", json=make_payload(names=["A", "B", "C", "D"])).json()

    # While certificate N was being generated, N-1 were already committed and the job was "processing".
    assert snapshots == [
        ("processing", 0, 4),
        ("processing", 1, 3),
        ("processing", 2, 2),
        ("processing", 3, 1),
    ]
    assert job["status"] == "completed"
    assert job["progress_percent"] == 100.0
    assert job["counts"] == {"total": 4, "succeeded": 4, "failed": 0, "pending": 0}
    assert job["started_at"] and job["finished_at"]


def test_a_stored_job_can_be_processed_later(client, make_payload):
    client.app.state.runner = NoopRunner()
    job_id = client.post("/jobs", json=make_payload()).json()["id"]

    client.app.state.process_job(job_id)

    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["progress_percent"] == 100.0


def test_reprocessing_a_finished_job_does_nothing(client, make_payload, tmp_path):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    before = sorted(p.name for p in (tmp_path / "certificates" / job_id).glob("*.pdf"))

    client.app.state.process_job(job_id)

    after = sorted(p.name for p in (tmp_path / "certificates" / job_id).glob("*.pdf"))
    assert before == after
    assert client.get(f"/jobs/{job_id}").json()["status"] == "completed"
