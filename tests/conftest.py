import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def make_client(tmp_path):
    """Factory for isolated apps (own SQLite file + storage dir). Inline mode by default."""
    opened = []

    def _make(generator=None, **overrides):
        params = dict(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            storage_dir=tmp_path / "certificates",
            processing_mode="inline",
        )
        params.update(overrides)
        client = TestClient(create_app(Settings(**params), generator=generator))
        client.__enter__()  # runs startup (lifespan) logic
        opened.append(client)
        return client

    yield _make
    for client in reversed(opened):
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def make_payload():
    def _make(names=("Asha Verma", "Rahul Mehta", "Priya Nair"), recipients=None, **certificate):
        info = {
            "course_name": "Advanced Python Workshop",
            "issuer": "Acme Learning Academy",
            "issue_date": "2026-10-01",
            "signatory_name": "Dr. Meera Iyer",
            "signatory_title": "Program Director",
        }
        info.update(certificate)
        if recipients is None:
            recipients = [{"name": n, "email": f"user{i}@example.com"} for i, n in enumerate(names)]
        return {"title": "Test job", "certificate": info, "recipients": recipients}

    return _make
