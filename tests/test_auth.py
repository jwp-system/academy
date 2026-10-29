from academy.models import Role
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
    assert "이번 달 미납" in resp.text


def test_teacher_cannot_access_admin_pages(client, login):
    login(Role.teacher)
    assert client.get("/").status_code == 200
    assert "이번 달 미납" not in client.get("/").text
    assert client.get("/billing").status_code == 403


def test_htmx_partial(client, login):
    login()
    resp = client.get("/students", headers={"HX-Request": "true"})
    assert "<html" not in resp.text
    assert "아직 등록된 학생이 없어요" in resp.text


def test_webhook_requires_token(client):
    payload = {"sender": "1588", "message": "입금"}
    assert client.post("/webhooks/sms-inbound", json=payload).status_code == 401
    resp = client.post(
        "/webhooks/sms-inbound", json=payload, headers={"X-Webhook-Token": "test-token"}
    )
    assert resp.json() == {"status": "ignored"}
