from app.models import Role
from tests.conftest import get_csrf


def test_redirects_to_login_when_anonymous(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_htmx_request_gets_hx_redirect(client):
    resp = client.get("/", headers={"HX-Request": "true"})
    assert resp.headers["HX-Redirect"] == "/login"


def test_login_rejects_bad_password(client, make_user):
    make_user()
    token = get_csrf(client)
    resp = client.post(
        "/login", data={"email": "admin@example.com", "password": "wrong", "csrf_token": token}
    )
    assert resp.status_code == 400


def test_login_requires_csrf(client, make_user):
    make_user()
    client.get("/login")
    resp = client.post("/login", data={"email": "admin@example.com", "password": "pw1234"})
    assert resp.status_code == 403


def test_admin_sees_dashboard(client, login):
    login()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "입금 확인 필요" in resp.text


def test_teacher_cannot_access_admin_pages(client, login):
    login(Role.teacher)
    assert client.get("/").status_code == 200
    assert "입금 확인 필요" not in client.get("/").text
    assert client.get("/billing").status_code == 403


def test_htmx_partial(client, login):
    login()
    resp = client.get("/students", headers={"HX-Request": "true"})
    assert "<html" not in resp.text
    assert "아직 등록된 학생이 없어요" in resp.text


def test_healthz_without_login(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {
        "status": "ok",
        "db": True,
        "scheduler": "disabled",
    }


def test_backup_download(client, login):
    import sqlite3
    import tempfile

    login()
    r = client.get("/system/backup")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        f.write(r.content)
        f.flush()
        tables = {
            t
            for (t,) in sqlite3.connect(f.name).execute(
                "select name from sqlite_master where type='table'"
            )
        }
    assert {"students", "invoices", "users"} <= tables


def test_backup_admin_only(client, login):
    from app.models import Role

    login(Role.teacher)
    assert client.get("/system/backup").status_code == 403


def test_refuses_default_secret_in_production(monkeypatch):
    import pytest

    from app.config import get_settings
    from app.main import create_app

    s = get_settings()
    monkeypatch.setattr(s, "cookie_secure", True)
    monkeypatch.setattr(s, "secret_key", "change-me")
    with pytest.raises(RuntimeError, match="ACADEMY_SECRET_KEY"):
        create_app()
