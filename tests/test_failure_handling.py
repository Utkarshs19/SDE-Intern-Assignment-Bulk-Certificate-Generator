"""One certificate failing must not stop the rest of the job."""
from app.pdf import render_certificate


def flaky_generator(bad_names, error=RuntimeError("renderer exploded")):
    def generate(data):
        if data.recipient_name in bad_names:
            raise error
        return render_certificate(data)

    return generate


def test_unexpected_failure_of_one_certificate_does_not_affect_the_others(make_client, make_payload):
    client = make_client(generator=flaky_generator({"Rahul Mehta"}))
    job = client.post("/jobs", json=make_payload(names=["Asha Verma", "Rahul Mehta", "Priya Nair"])).json()

    assert job["status"] == "completed_with_errors"
    assert job["counts"] == {"total": 3, "succeeded": 2, "failed": 1, "pending": 0}
    assert job["failures"] == [
        {
            "position": 1,
            "recipient_name": "Rahul Mehta",
            "recipient_email": "user1@example.com",
            "error": "unexpected error (RuntimeError): renderer exploded",
        }
    ]

    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    assert [i["status"] for i in items] == ["succeeded", "failed", "succeeded"]
    assert items[1]["download_url"] is None


def test_failures_in_the_middle_do_not_stop_later_certificates(make_client, make_payload):
    names = [f"Person {i}" for i in range(10)]
    client = make_client(generator=flaky_generator({"Person 0", "Person 4", "Person 9"}))
    job = client.post("/jobs", json=make_payload(names=names)).json()

    assert job["counts"] == {"total": 10, "succeeded": 7, "failed": 3, "pending": 0}
    assert [f["position"] for f in job["failures"]] == [0, 4, 9]


def test_real_renderer_failure_is_isolated(client, make_payload):
    """Uses the real generator: a name with characters the built-in font can't draw."""
    job = client.post("/jobs", json=make_payload(names=["Asha Verma", "रमेश कुमार", "Priya Nair"])).json()

    assert job["status"] == "completed_with_errors"
    assert job["counts"]["succeeded"] == 2
    assert job["failures"][0]["position"] == 1
    assert "cannot render" in job["failures"][0]["error"]


def test_job_where_every_certificate_fails_is_marked_failed(make_client, make_payload):
    client = make_client(generator=flaky_generator({"A", "B"}))
    job = client.post("/jobs", json=make_payload(names=["A", "B"])).json()

    assert job["status"] == "failed"
    assert job["counts"] == {"total": 2, "succeeded": 0, "failed": 2, "pending": 0}
    assert client.get(f"/jobs/{job['id']}/download").status_code == 409


def test_failed_certificate_cannot_be_downloaded_and_explains_why(make_client, make_payload):
    client = make_client(generator=flaky_generator({"Rahul Mehta"}))
    job = client.post("/jobs", json=make_payload()).json()
    failed = client.get(f"/jobs/{job['id']}/certificates", params={"status": "failed"}).json()["items"][0]

    resp = client.get(f"/jobs/{job['id']}/certificates/{failed['id']}/download")
    assert resp.status_code == 409
    assert "renderer exploded" in resp.json()["detail"]


def test_failures_listing_is_capped_in_the_job_summary(make_client, make_payload):
    client = make_client()
    job = client.post("/jobs", json=make_payload(recipients=[{} for _ in range(150)])).json()

    assert job["counts"]["failed"] == 150
    assert len(job["failures"]) == 100
    assert job["failures_truncated"] is True
    # The complete list is still available through the paginated endpoint.
    listing = client.get(f"/jobs/{job['id']}/certificates", params={"status": "failed", "limit": 500}).json()
    assert listing["total"] == 150
