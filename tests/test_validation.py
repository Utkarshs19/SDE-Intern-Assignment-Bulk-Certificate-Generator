"""Input validation: request-level (whole request rejected) and recipient-level (row fails alone)."""
import pytest

from app.validation import validate_recipient


# --- request level -> 4xx, nothing is stored ---------------------------------

@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("certificate"),
        lambda p: p["certificate"].pop("course_name"),
        lambda p: p["certificate"].pop("issuer"),
        lambda p: p["certificate"].update(course_name="   "),
        lambda p: p["certificate"].update(issue_date="not-a-date"),
        lambda p: p.pop("recipients"),
        lambda p: p.update(recipients=[]),
        lambda p: p.update(recipients="Asha Verma"),
    ],
    ids=[
        "missing certificate",
        "missing course_name",
        "missing issuer",
        "blank course_name",
        "bad issue_date",
        "missing recipients",
        "empty recipients",
        "recipients not a list",
    ],
)
def test_malformed_request_is_rejected(client, make_payload, mutate):
    payload = make_payload()
    mutate(payload)
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_is_rejected(make_client, make_payload):
    client = make_client(max_recipients_per_job=2)
    resp = client.post("/jobs", json=make_payload(names=["A", "B", "C"]))
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]


def test_template_text_the_font_cannot_render_is_rejected(client, make_payload):
    resp = client.post("/jobs", json=make_payload(course_name="पायथन कार्यशाला"))
    assert resp.status_code == 422
    assert "course name" in resp.json()["detail"] or "course_name" in resp.json()["detail"]


# --- recipient level (unit) --------------------------------------------------

def test_valid_recipient_is_normalised():
    recipient, error = validate_recipient(
        {"name": "  Asha Verma ", "email": "asha@example.com", "achievement": "with Distinction", "extra": 1}
    )
    assert error is None
    assert recipient.name == "Asha Verma"
    assert recipient.email == "asha@example.com"
    assert recipient.achievement == "with Distinction"


def test_blank_optional_fields_become_none():
    recipient, error = validate_recipient({"name": "Asha", "email": "  ", "achievement": ""})
    assert error is None
    assert recipient.email is None and recipient.achievement is None


@pytest.mark.parametrize(
    "raw, expected_fragment",
    [
        ({}, "name"),
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": None}, "name"),
        ({"name": 42}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"name": "Line\nBreak"}, "control characters"),
        ({"name": "Asha", "email": "not-an-email"}, "email"),
        ({"name": "Asha", "achievement": "y" * 101}, "achievement"),
        ("Asha Verma", "JSON object"),
        (None, "JSON object"),
        (["Asha"], "JSON object"),
    ],
)
def test_invalid_recipients_are_described(raw, expected_fragment):
    recipient, error = validate_recipient(raw)
    assert recipient is None
    assert expected_fragment in error


# --- recipient level (through the API) ----------------------------------------

def test_invalid_recipients_fail_individually_and_valid_ones_still_generate(client, make_payload):
    payload = make_payload(
        recipients=[
            {"name": "Asha Verma", "email": "asha@example.com"},
            {"name": "", "email": "blank@example.com"},
            {"name": "Rahul Mehta", "email": "bad-email"},
            "just a string",
            {"name": "Priya Nair"},
        ]
    )
    job = client.post("/jobs", json=payload).json()
    assert job["status"] == "completed_with_errors"
    assert job["counts"] == {"total": 5, "succeeded": 2, "failed": 3, "pending": 0}

    failures = {f["position"]: f for f in job["failures"]}
    assert set(failures) == {1, 2, 3}
    assert "name" in failures[1]["error"]
    assert "email" in failures[2]["error"]
    assert failures[2]["recipient_name"] == "Rahul Mehta"  # kept so the client can identify the row
    assert "JSON object" in failures[3]["error"]


def test_duplicate_email_in_one_request_is_flagged(client, make_payload):
    payload = make_payload(
        recipients=[
            {"name": "Asha Verma", "email": "asha@example.com"},
            {"name": "Asha V.", "email": "ASHA@example.com"},
        ]
    )
    job = client.post("/jobs", json=payload).json()
    assert job["counts"]["succeeded"] == 1
    assert job["failures"][0]["position"] == 1
    assert "duplicate" in job["failures"][0]["error"]


def test_job_with_only_invalid_recipients_fails_without_processing(client, make_payload):
    job = client.post("/jobs", json=make_payload(recipients=[{}, {"name": ""}])).json()
    assert job["status"] == "failed"
    assert job["counts"] == {"total": 2, "succeeded": 0, "failed": 2, "pending": 0}
    assert job["finished_at"] is not None
    assert job["progress_percent"] == 100.0
