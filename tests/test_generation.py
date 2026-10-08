"""Certificate generation itself."""
from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.pdf import CertificateData, CertificateGenerationError, render_certificate


def _data(**overrides):
    base = dict(
        recipient_name="Asha Verma",
        course_name="Advanced Python Workshop",
        issuer="Acme Learning Academy",
        issue_date=date(2026, 10, 1),
        certificate_number="CERT-ABCD1234-00001",
        achievement="with Distinction",
        signatory_name="Dr. Meera Iyer",
        signatory_title="Program Director",
    )
    base.update(overrides)
    return CertificateData(**base)


def _text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    return reader.pages[0].extract_text()


def test_renders_a_single_page_pdf_with_recipient_specific_details():
    pdf = render_certificate(_data())

    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    for expected in [
        "Asha Verma",
        "Advanced Python Workshop",
        "Acme Learning Academy",
        "1 October 2026",
        "with Distinction",
        "Dr. Meera Iyer",
        "CERT-ABCD1234-00001",
    ]:
        assert expected in text


def test_different_recipients_get_different_certificates():
    a = _text(render_certificate(_data(recipient_name="Asha Verma")))
    b = _text(render_certificate(_data(recipient_name="Rahul Mehta")))
    assert "Asha Verma" in a and "Rahul Mehta" not in a
    assert "Rahul Mehta" in b and "Asha Verma" not in b


def test_optional_parts_can_be_omitted():
    text = _text(render_certificate(_data(achievement=None, signatory_name=None, signatory_title=None)))
    assert "Asha Verma" in text
    assert "Program Director" not in text


def test_long_names_and_course_names_still_fit():
    long_name = "Maximilian Alexander Bartholomew Montgomery-Fitzgerald Wellington III"
    long_course = "Introduction to Distributed Systems, Cloud Infrastructure and Site Reliability " * 1
    text = _text(render_certificate(_data(recipient_name=long_name, course_name=long_course.strip())))
    assert long_name in text


def test_text_that_cannot_fit_raises():
    with pytest.raises(CertificateGenerationError, match="too long"):
        render_certificate(_data(recipient_name="W" * 100))


def test_unsupported_characters_raise_a_clear_error():
    with pytest.raises(CertificateGenerationError, match="cannot render"):
        render_certificate(_data(recipient_name="रमेश कुमार"))


def test_job_run_writes_a_real_pdf_per_valid_recipient(client, make_payload, tmp_path):
    job = client.post("/jobs", json=make_payload()).json()
    assert job["status"] == "completed"

    files = sorted((tmp_path / "certificates" / job["id"]).glob("*.pdf"))
    assert len(files) == 3
    assert all(f.read_bytes().startswith(b"%PDF") for f in files)


def test_certificate_numbers_are_unique_and_traceable(client, make_payload):
    job = client.post("/jobs", json=make_payload()).json()
    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    numbers = [i["certificate_number"] for i in items]
    assert len(set(numbers)) == 3
    assert numbers[0] == f"CERT-{job['id'][:8].upper()}-00001"
