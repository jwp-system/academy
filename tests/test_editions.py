import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import edition
from app.config import get_settings
from app.main import create_app
from app.models import BankTransaction, Channel, Invoice, Notification, Student, User
from app.scheduler import create_scheduler
from app.services.notify import channels


@pytest.fixture(autouse=True)
def restore_edition():
    yield
    edition.activate(edition.BASE)


def _csrf(client) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)


# --- 데모판 -------------------------------------------------------------------


@pytest.fixture
def demo_client(db):
    from demo import seed
    from demo.edition import build

    ed = build()
    ed.on_startup = None  # 테스트 DB는 demo*.db가 아니라서 직접 채운다
    app = create_app(ed)
    seed.seed(db)
    with TestClient(app) as c:
        yield c


def test_demo_never_sends_for_real(demo_client, monkeypatch):
    s = get_settings()
    for k, v in {
        "sms_mode": "live",
        "sms_gateway_user": "u",
        "sms_gateway_password": "p",
        "email_mode": "live",
    }.items():
        monkeypatch.setattr(s, k, v)
    assert channels.mode(Channel.sms) == "test" and channels.mode(Channel.email) == "test"
    assert channels.adapter_for(Channel.sms) is channels.TEST


def test_demo_has_no_webhook(demo_client):
    assert demo_client.post("/webhooks/sms-inbound", content=b"{}").status_code == 404


def test_demo_login_page_and_quick_login(demo_client, db):
    page = demo_client.get("/login").text
    assert "원장으로 둘러보기" in page and "demo-banner" in page
    token = _csrf(demo_client)
    r = demo_client.post("/demo/login/admin", data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    home = demo_client.get("/").text
    assert "김원장님, 안녕하세요" in home and "할 일" in home
    assert "오늘은 수업이 없어요" not in home  # 주말에도 특강이 있다
    assert "청구 안내 문자를 보내지 않은 청구" not in home
    assert (
        demo_client.post("/demo/login/nobody", data={"csrf_token": _csrf(demo_client)}).status_code
        == 404
    )


def test_demo_seed_fills_every_screen(demo_client, db):
    assert len(db.exec(select(Student)).all()) == 24
    assert db.exec(select(Invoice).where(Invoice.status == "paid")).first()
    statuses = {t.match_status for t in db.exec(select(BankTransaction)).all()}
    assert {"auto", "review", "unmatched"} <= statuses
    assert db.exec(
        select(Notification).where(Notification.provider == "test", Notification.status == "sent")
    ).first()
    assert db.exec(select(Notification).where(Notification.status == "failed")).first()
    # 실제 번호가 섞이지 않았는지: 보호자 번호는 전부 0100000xxxx
    from app.models import Guardian

    assert all(g.phone.startswith("0100000") for g in db.exec(select(Guardian)).all() if g.phone)

    demo_client.post("/demo/login/admin", data={"csrf_token": _csrf(demo_client)})
    for url in [
        "/",
        "/students",
        "/students/1",
        "/teachers",
        "/classes",
        "/classes/1",
        "/classes/calendar",
        "/attendance",
        "/billing",
        "/billing/receipts",
        "/billing/deposits",
        "/notifications",
        "/notifications/templates",
        "/notifications/test",
    ]:
        r = demo_client.get(url)
        assert r.status_code == 200 and "</html>" in r.text, url
    demo_client.post("/logout", data={"csrf_token": _csrf(demo_client)})
    demo_client.post("/demo/login/teacher", data={"csrf_token": _csrf(demo_client)})
    assert "이수학님" in demo_client.get("/").text
    assert demo_client.get("/billing").status_code == 403


def test_demo_reset_refuses_non_demo_db():
    from demo import seed

    with pytest.raises(RuntimeError, match="demo"):
        seed.reset()


def test_demo_prepare_env_ignores_academy_db(monkeypatch):
    import os

    from demo import DEFAULT_DB, prepare_env

    monkeypatch.setenv("ACADEMY_DATABASE_URL", "sqlite:///real/academy.db")
    monkeypatch.delenv("DEMO_DATABASE_URL", raising=False)
    prepare_env()
    assert os.environ["ACADEMY_DATABASE_URL"] == f"sqlite:///{DEFAULT_DB}"
    monkeypatch.setenv("DEMO_DATABASE_URL", "sqlite:////srv/demo/data/demo.db")
    prepare_env()
    assert os.environ["ACADEMY_DATABASE_URL"] == "sqlite:////srv/demo/data/demo.db"


def test_demo_schedules_daily_reset():
    from demo.edition import build

    assert "demo_reset" in {j.id for j in create_scheduler(build().add_jobs).get_jobs()}


# --- 구축판 -------------------------------------------------------------------


@pytest.fixture
def academy_admin(db, login, monkeypatch, tmp_path):
    from academy.edition import build

    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path / "backups"))
    app = create_app(build())
    with TestClient(app) as c:
        c.post("/login")  # 세션 생성
        from app.auth import hash_password

        db.add(
            User(
                name="원장", email="a@x.com", password_hash=hash_password("pw123456"), role="admin"
            )
        )
        db.commit()
        r = c.post(
            "/login",
            data={"email": "a@x.com", "password": "pw123456", "csrf_token": _csrf(c)},
            follow_redirects=False,
        )
        assert r.status_code == 303
        token = re.search(r'"X-CSRF-Token": "([^"]+)"', c.get("/").text).group(1)
        c.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
        yield c, tmp_path / "backups"


def test_academy_backup_screen(academy_admin):
    c, folder = academy_admin
    page = c.get("/backups", headers={"HX-Request": ""}).text
    assert "아직 백업이 없어요" in page and "최근 이틀 동안 백업이 없어요" in page
    assert 'href="/backups"' in c.get("/", headers={"HX-Request": ""}).text  # 원장 메뉴에 백업
    c.post("/backups/run")
    files = list(folder.glob("academy-*.db"))
    assert len(files) == 1
    r = c.get(f"/backups/{files[0].name}")
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
    assert c.get("/backups/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.get("/backups/notabackup.db").status_code == 404


def test_academy_backup_retention(tmp_path, monkeypatch):
    from app.services import backup

    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "backup_keep_days", 30)
    old = tmp_path / f"academy-{(datetime.now() - timedelta(days=40)):%Y%m%d-%H%M}.db"
    old.write_bytes(b"x")
    keep = tmp_path / f"academy-{(datetime.now() - timedelta(days=3)):%Y%m%d-%H%M}.db"
    keep.write_bytes(b"x")
    made = backup.run_scheduled()
    assert made.exists() and keep.exists() and not old.exists()
    assert backup.latest(Path(tmp_path)) == made


def test_academy_edition_jobs_and_webhook():
    from academy.edition import build

    ed = build()
    assert ed.allow_live_notifications and ed.allow_webhooks
    assert "backup" in {j.id for j in create_scheduler(ed.add_jobs).get_jobs()}


def test_demo_landing_is_intro(demo_client):
    r = demo_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/intro"
    page = demo_client.get("/intro").text
    assert "운영비 0원" in page and "원장으로 둘러보기" in page and "무엇부터 볼까요?" in page
    assert demo_client.get("/login").status_code == 200  # 로그인 화면은 그대로


def test_demo_scenario_login_opens_screen(demo_client):
    token = _csrf(demo_client)
    r = demo_client.post(
        "/demo/login/admin?next=/billing/deposits",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/billing/deposits"
    assert "대시보드로 가기" in demo_client.get("/intro").text  # 로그인하면 체험 버튼 대신
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com"):
        r = demo_client.post(
            f"/demo/login/admin?next={bad}",
            data={"csrf_token": _csrf(demo_client)},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/", bad
    r = demo_client.post("/logout", data={"csrf_token": _csrf(demo_client)}, follow_redirects=True)
    assert r.url.path == "/intro"


def test_academy_landing_is_login(client):
    from academy.edition import build

    with TestClient(create_app(build())) as c:
        r = c.get("/", follow_redirects=False)
        assert r.headers["location"] == "/login"
        assert c.get("/intro").status_code == 404
