import json
from datetime import date, time, timedelta

import pytest
from sqlmodel import select

from app.models import (
    ClassGroup,
    ClassSchedule,
    ClassSession,
    Enrollment,
    Role,
    SessionStatus,
    User,
)
from app.schemas import StudentIn
from app.services.students import create_student


@pytest.fixture
def admin(client, login):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return client


def _triggers(resp) -> dict:
    return json.loads(resp.headers.get("HX-Trigger", "{}"))


def _make_class(admin, db, **data) -> ClassGroup:
    resp = admin.post("/classes", data={"name": "중2 수학", "room": "1강", **data})
    assert "classSaved" in _triggers(resp), resp.text
    return db.exec(select(ClassGroup).order_by(ClassGroup.id.desc())).first()


def _next_weekday(wd: int) -> date:
    today = date.today()
    return today + timedelta(days=(wd - today.weekday()) % 7)


def test_create_class_parses_fee(admin, db):
    c = _make_class(admin, db, monthly_fee="280,000", capacity="8")
    assert c.monthly_fee == 280000 and c.capacity == 8 and c.active


def test_add_schedule_generates_sessions(admin, db):
    c = _make_class(admin, db)
    resp = admin.post(
        f"/classes/{c.id}/schedules",
        data={"weekday": "0", "start_time": "16:00", "end_time": "18:00"},
    )
    assert "회차 4개" in _triggers(resp)["toast"]
    sessions = db.exec(select(ClassSession)).all()
    assert len(sessions) == 4
    assert sessions[0].date == _next_weekday(0)


def test_schedule_time_order_validation(admin, db):
    c = _make_class(admin, db)
    resp = admin.post(
        f"/classes/{c.id}/schedules",
        data={"weekday": "0", "start_time": "18:00", "end_time": "16:00"},
    )
    assert "끝나는 시각이 시작 시각보다 늦어야 해요." in resp.text
    assert db.exec(select(ClassSchedule)).first() is None


def test_schedule_conflict_warns_then_force(admin, db):
    a = _make_class(admin, db, name="A")
    admin.post(
        f"/classes/{a.id}/schedules",
        data={"weekday": "1", "start_time": "16:00", "end_time": "18:00"},
    )
    b = _make_class(admin, db, name="B")  # 같은 강의실
    data = {"weekday": "1", "start_time": "17:00", "end_time": "19:00"}
    resp = admin.post(f"/classes/{b.id}/schedules", data=data)
    assert "시간이 겹쳐요" in resp.text and "강의실 겹침 · A" in resp.text
    assert 'name="force" value="1"' in resp.text
    assert len(db.exec(select(ClassSchedule)).all()) == 1
    resp = admin.post(f"/classes/{b.id}/schedules", data=data | {"force": "1"})
    assert "시간이 겹쳐요" not in resp.text
    assert len(db.exec(select(ClassSchedule)).all()) == 2


def test_delete_schedule_removes_future_sessions(admin, db):
    c = _make_class(admin, db)
    admin.post(
        f"/classes/{c.id}/schedules",
        data={"weekday": "3", "start_time": "16:00", "end_time": "18:00"},
    )
    sched = db.exec(select(ClassSchedule)).one()
    resp = admin.delete(f"/classes/{c.id}/schedules/{sched.id}")
    assert "회차 4개도 지웠어요" in _triggers(resp)["toast"]
    assert db.exec(select(ClassSession)).first() is None


def test_change_teacher_syncs_and_deactivate_removes(admin, db):
    t = User(name="이수학", email="lee@x.com", password_hash="h", role=Role.teacher)
    db.add(t)
    db.commit()
    c = _make_class(admin, db)
    admin.post(
        f"/classes/{c.id}/schedules",
        data={"weekday": "4", "start_time": "16:00", "end_time": "18:00"},
    )
    resp = admin.put(
        f"/classes/{c.id}",
        data={"name": "중2 수학", "room": "2강", "teacher_id": str(t.id), "active": "on"},
    )
    assert "회차 4개에 반영했어요" in _triggers(resp)["toast"]
    assert {(s.room, s.teacher_id) for s in db.exec(select(ClassSession)).all()} == {("2강", t.id)}

    resp = admin.put(
        f"/classes/{c.id}", data={"name": "중2 수학", "room": "2강", "teacher_id": str(t.id)}
    )
    assert "회차 4개를 지웠어요" in _triggers(resp)["toast"]
    assert db.exec(select(ClassSession)).first() is None


def test_enroll_and_end(admin, db):
    c = _make_class(admin, db)
    s = create_student(db, StudentIn(name="홍길동"))
    db.commit()
    assert "홍길동" in admin.get(f"/classes/{c.id}/enroll-search?q=홍").text
    resp = admin.post(f"/classes/{c.id}/enrollments", data={"student_id": str(s.id)})
    assert "수강 중 1명" in resp.text
    assert (
        admin.post(f"/classes/{c.id}/enrollments", data={"student_id": str(s.id)}).status_code
        == 409
    )
    assert "추가할 수 있는 학생이 없어요" in admin.get(f"/classes/{c.id}/enroll-search").text

    e = db.exec(select(Enrollment)).one()
    resp = admin.post(f"/classes/{c.id}/enrollments/{e.id}/end")
    db.refresh(e)
    assert e.end_date == date.today()  # 오늘까지는 수강 중


def test_makeup_cancel_restore(admin, db):
    c = _make_class(admin, db)
    day = (date.today() + timedelta(days=3)).isoformat()
    resp = admin.post(
        f"/classes/{c.id}/sessions",
        data={"date": day, "start_time": "10:00", "end_time": "12:00", "room": "1강"},
    )
    assert "보강 회차를 만들었어요" in _triggers(resp)["toast"]
    s = db.exec(select(ClassSession)).one()
    assert s.status == SessionStatus.makeup and s.schedule_id is None

    admin.post(f"/classes/sessions/{s.id}/cancel")
    db.refresh(s)
    assert s.status == SessionStatus.cancelled
    admin.post(f"/classes/sessions/{s.id}/restore")
    db.refresh(s)
    assert s.status == SessionStatus.makeup  # 수동 회차는 보강으로 복구


def test_session_edit_conflict(admin, db):
    a = _make_class(admin, db, name="A")
    b = _make_class(admin, db, name="B", room="2강")
    day = date.today() + timedelta(days=1)
    for cls, room in [(a, "1강"), (b, "2강")]:
        db.add(
            ClassSession(
                class_id=cls.id, date=day, start_time=time(16), end_time=time(18), room=room
            )
        )
    db.commit()
    sb = db.exec(select(ClassSession).where(ClassSession.class_id == b.id)).one()
    data = {"date": day.isoformat(), "start_time": "16:00", "end_time": "18:00", "room": "1강"}
    resp = admin.put(f"/classes/sessions/{sb.id}", data=data)
    assert "강의실 겹침 · A" in resp.text
    resp = admin.put(f"/classes/sessions/{sb.id}", data=data | {"force": "1"})
    assert "sessionSaved" in _triggers(resp)


def test_events_feed(admin, db):
    c = _make_class(admin, db)
    admin.post(
        f"/classes/{c.id}/schedules",
        data={"weekday": "0", "start_time": "16:00", "end_time": "18:00"},
    )
    mon = _next_weekday(0)
    resp = admin.get(
        f"/classes/events?start={mon.isoformat()}T00:00:00%2B09:00&end={(mon + timedelta(days=7)).isoformat()}T00:00:00%2B09:00"
    )
    events = resp.json()
    assert len(events) == 1
    assert events[0]["start"] == f"{mon.isoformat()}T16:00" and events[0]["title"] == "중2 수학"
    assert admin.get("/classes/events?start=x&end=y").status_code == 400


def test_full_pages_render(admin, db):
    c = _make_class(admin, db)
    admin.post(
        f"/classes/{c.id}/schedules",
        data={"weekday": "0", "start_time": "16:00", "end_time": "18:00"},
    )
    plain = {"HX-Request": ""}
    for url in ["/classes", f"/classes/{c.id}", "/classes/calendar"]:
        r = admin.get(url, headers=plain)
        assert r.status_code == 200 and "</html>" in r.text, url
    s = db.exec(select(ClassSession)).first()
    for url in [
        "/classes/new",
        f"/classes/{c.id}/edit",
        f"/classes/{c.id}/sessions/new",
        f"/classes/sessions/{s.id}/edit",
    ]:
        assert admin.get(url).status_code == 200, url


# --- 강사 권한 ------------------------------------------------------------------


def test_teacher_sees_only_own_classes_read_only(client, login, db):
    token = login(Role.teacher)
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    me = db.exec(select(User).where(User.role == Role.teacher)).one()
    mine = ClassGroup(name="내반", teacher_id=me.id)
    other = ClassGroup(name="남의반")
    db.add_all([mine, other])
    db.flush()
    day = date.today() + timedelta(days=1)
    db.add(
        ClassSession(
            class_id=other.id, date=day, start_time=time(10), end_time=time(11), teacher_id=me.id
        )
    )  # 대강
    db.add(ClassSession(class_id=other.id, date=day, start_time=time(12), end_time=time(13)))
    db.commit()

    rows = client.get("/classes").text
    assert "내반" in rows and "남의반" not in rows
    assert client.get(f"/classes/{other.id}").status_code == 404
    assert client.post("/classes", data={"name": "x"}).status_code == 403
    assert client.post("/classes/generate").status_code == 403

    ev = client.get(f"/classes/events?start={day}&end={day + timedelta(days=1)}").json()
    assert [e["start"][-5:] for e in ev] == ["10:00"]  # 대강 회차만


def test_overlapping_slot_in_same_class_is_error(admin, db):
    c = _make_class(admin, db)
    data = {"weekday": "1", "start_time": "16:00", "end_time": "18:00"}
    admin.post(f"/classes/{c.id}/schedules", data=data)
    resp = admin.post(f"/classes/{c.id}/schedules", data=data | {"force": "1"})
    assert "이 반에 이미 겹치는 시간표가 있어요." in resp.text
    assert len(db.exec(select(ClassSchedule)).all()) == 1
