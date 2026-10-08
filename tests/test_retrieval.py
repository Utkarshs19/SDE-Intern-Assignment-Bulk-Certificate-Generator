"""Retrieving generated certificates."""
import io
import zipfile
from io import BytesIO

from pypdf import PdfReader


def _pdf_text(content: bytes) -> str:
    return PdfReader(BytesIO(content)).pages[0].extract_text()


def test_download_single_certificate(client, make_payload):
    job = client.post("/jobs", json=make_payload()).json()
    item = client.get(f"/jobs/{job['id']}/certificates").json()["items"][1]
    assert item["download_url"] == f"/jobs/{job['id']}/certificates/{item['id']}/download"

    resp = client.get(item["download_url"])

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert item["certificate_number"] in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")
    assert "Rahul Mehta" in _pdf_text(resp.content)


def test_download_all_as_zip(client, make_payload):
    job = client.post("/jobs", json=make_payload()).json()

    resp = client.get(job["links"]["download_all"])

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert len(names) == 3
        assert all(n.endswith(".pdf") for n in names)
        texts = [_pdf_text(zf.read(n)) for n in names]
    assert any("Asha Verma" in t for t in texts)
    assert any("Priya Nair" in t for t in texts)


def test_zip_only_contains_successful_certificates(client, make_payload):
    job = client.post("/jobs", json=make_payload(recipients=[{"name": "Asha Verma"}, {"name": ""}])).json()
    resp = client.get(job["links"]["download_all"])
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert len(zf.namelist()) == 1


def test_list_certificates_supports_filtering_and_pagination(client, make_payload):
    names = [f"Person {i}" for i in range(5)]
    recipients = [{"name": n} for n in names] + [{"name": ""}]
    job_id = client.post("/jobs", json=make_payload(recipients=recipients)).json()["id"]
    url = f"/jobs/{job_id}/certificates"

    assert client.get(url).json()["total"] == 6
    assert client.get(url, params={"status": "succeeded"}).json()["total"] == 5
    failed = client.get(url, params={"status": "failed"}).json()
    assert failed["total"] == 1 and failed["items"][0]["position"] == 5

    page = client.get(url, params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 6
    assert [i["recipient_name"] for i in page["items"]] == ["Person 2", "Person 3"]


def test_list_rejects_bad_query_parameters(client, make_payload):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    url = f"/jobs/{job_id}/certificates"
    assert client.get(url, params={"status": "bogus"}).status_code == 422
    assert client.get(url, params={"limit": 0}).status_code == 422
    assert client.get(url, params={"limit": 501}).status_code == 422
    assert client.get(url, params={"offset": -1}).status_code == 422


def test_certificate_from_another_job_is_not_reachable(client, make_payload):
    job_a = client.post("/jobs", json=make_payload()).json()["id"]
    job_b = client.post("/jobs", json=make_payload()).json()["id"]
    cert_of_a = client.get(f"/jobs/{job_a}/certificates").json()["items"][0]["id"]

    assert client.get(f"/jobs/{job_b}/certificates/{cert_of_a}/download").status_code == 404
    assert client.get(f"/jobs/{job_a}/certificates/nope/download").status_code == 404


def test_missing_file_on_disk_returns_410(client, make_payload, tmp_path):
    job_id = client.post("/jobs", json=make_payload()).json()["id"]
    item = client.get(f"/jobs/{job_id}/certificates").json()["items"][0]
    for pdf in (tmp_path / "certificates" / job_id).glob("*.pdf"):
        pdf.unlink()

    assert client.get(item["download_url"]).status_code == 410
