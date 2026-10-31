import json

import pytest
from sqlmodel import select

from app.auth import verify_password
from app.models import Role, User


@pytest.fixture
def admin(client, login, db):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return db.exec(select(User)).one()


def test_create_teacher(client, admin, db):
    resp = client.post(
        "/teachers",
        data={
            "name": "김강사",
            "email": "Kim@Example.com",
            "subject": "수학",
            "role": "teacher",
            "active": "on",
            "password": "password1",
        },
    )
    assert "teacherSaved" in json.loads(resp.headers["HX-Trigger"])
    t = db.exec(select(User).where(User.name == "김강사")).one()
    assert t.email == "kim@example.com" and t.role == Role.teacher and t.active
    assert verify_password(t.password_hash, "password1")


def test_create_teacher_validation(client, admin):
    resp = client.post(
        "/teachers", data={"name": "x", "email": "admin@example.com", "role": "teacher"}
    )
    assert "꼭 입력해 주세요." in resp.text  # 비밀번호
    resp = client.post(
        "/teachers",
        data={
            "name": "x",
            "email": "admin@example.com",
            "role": "teacher",
            "password": "password1",
        },
    )
    assert "이미 등록된 이메일이에요." in resp.text
    resp = client.post("/teachers", data={"name": "x", "email": "y@z.com", "password": "short"})
    assert "8자 이상" in resp.text


def test_update_keeps_password_when_blank(client, admin, db):
    t = User(name="이강사", email="lee@example.com", password_hash="h", role=Role.teacher)
    db.add(t)
    db.commit()
    client.put(
        f"/teachers/{t.id}", data={"name": "이강사", "email": "lee@example.com", "role": "teacher"}
    )
    db.refresh(t)
    assert t.password_hash == "h"
    assert t.active is False  # 체크박스 해제 = 비활성화


def test_admin_cannot_demote_or_deactivate_self(client, admin, db):
    resp = client.put(
        f"/teachers/{admin.id}",
        data={"name": "원장", "email": admin.email, "role": "teacher", "active": "on"},
    )
    assert "본인 계정은" in resp.text
    db.refresh(admin)
    assert admin.role == Role.admin


def test_inactive_hidden_by_default(client, admin, db):
    db.add(User(name="퇴사강사", email="old@example.com", password_hash="h", active=False))
    db.commit()
    assert "퇴사강사" not in client.get("/teachers").text
    assert "퇴사강사" in client.get("/teachers?show_inactive=true").text


def test_deactivated_user_cannot_login(client, make_user, db):
    from tests.conftest import get_csrf

    u = make_user(email="t@example.com", role=Role.teacher)
    u.active = False
    db.add(u)
    db.commit()
    resp = client.post(
        "/login",
        data={"email": "t@example.com", "password": "pw1234", "csrf_token": get_csrf(client)},
    )
    assert resp.status_code == 400
