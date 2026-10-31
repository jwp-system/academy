from datetime import date, time, timedelta

from sqlmodel import select

from app.models import (
    Attendance,
    ClassGroup,
    ClassSchedule,
    ClassSession,
    SessionStatus,
    Student,
    User,
)
from app.services import sessions as svc

MON = date(2026, 11, 2)  # 월요일


def _class(db, name="중2 수학", room="1강", teacher_id=None, slots=((0, 16, 18),)):
    c = ClassGroup(name=name, room=room, teacher_id=teacher_id)
    db.add(c)
    db.flush()
    for wd, s, e in slots:
        db.add(ClassSchedule(class_id=c.id, weekday=wd, start_time=time(s), end_time=time(e)))
    db.commit()
    return c


def _sessions(db, class_id=None):
    stmt = select(ClassSession).order_by(ClassSession.date)
    if class_id:
        stmt = stmt.where(ClassSession.class_id == class_id)
    return db.exec(stmt).all()


def test_generate_four_weeks_and_idempotent(db):
    c = _class(db, slots=((0, 16, 18), (2, 16, 18)))  # 월, 수
    assert svc.generate_sessions(db, MON) == 8
    db.commit()
    assert svc.generate_sessions(db, MON) == 0
    rows = _sessions(db)
    assert rows[0].date == MON and rows[1].date == MON + timedelta(days=2)
    assert all(s.room == "1강" and s.schedule_id for s in rows)
    assert all(s.class_id == c.id for s in rows)


def test_generate_starts_midweek(db):
    _class(db, slots=((0, 16, 18),))
    wed = MON + timedelta(days=2)
    svc.generate_sessions(db, wed, weeks=1)
    assert [s.date for s in _sessions(db)] == [MON + timedelta(days=7)]


def test_cancelled_session_not_regenerated(db):
    _class(db)
    svc.generate_sessions(db, MON, weeks=1)
    s = _sessions(db)[0]
    s.status = SessionStatus.cancelled
    db.commit()
    assert svc.generate_sessions(db, MON, weeks=1) == 0


def test_inactive_class_skipped(db):
    c = _class(db)
    c.active = False
    db.commit()
    assert svc.generate_sessions(db, MON) == 0


def test_remove_future_keeps_attended_and_modified(db):
    c = _class(db)
    svc.generate_sessions(db, MON)
    db.commit()
    rows = _sessions(db)
    stu = Student(name="a", view_token="t", payer_code="a1")
    db.add(stu)
    db.flush()
    db.add(Attendance(session_id=rows[1].id, student_id=stu.id))
    rows[2].status = SessionStatus.cancelled
    db.commit()
    assert svc.remove_future_sessions(db, MON, class_id=c.id) == 2
    db.commit()
    assert [s.id for s in _sessions(db)] == [rows[1].id, rows[2].id]


def test_sync_future_keeps_individual_substitute(db):
    t1 = User(name="김", email="a@x.com", password_hash="h")
    t2 = User(name="이", email="b@x.com", password_hash="h")
    sub = User(name="박", email="c@x.com", password_hash="h")
    db.add_all([t1, t2, sub])
    db.flush()
    c = _class(db, teacher_id=t1.id)
    svc.generate_sessions(db, MON, weeks=2)
    db.commit()
    first, second = _sessions(db)
    second.teacher_id = sub.id  # 대강
    db.commit()
    c.teacher_id = t2.id
    assert svc.sync_future_sessions(db, c, MON, old_room="1강", old_teacher_id=t1.id) == 1
    db.commit()
    db.refresh(first)
    db.refresh(second)
    assert first.teacher_id == t2.id and second.teacher_id == sub.id


def test_schedule_conflicts_room_and_teacher(db):
    t = User(name="김", email="a@x.com", password_hash="h")
    db.add(t)
    db.flush()
    _class(db, name="A", room="1강", teacher_id=t.id, slots=((0, 16, 18),))
    # 같은 강의실, 겹치는 시간
    out = svc.schedule_conflicts(
        db, weekday=0, start=time(17), end=time(19), room="1강", teacher_id=None
    )
    assert [c.reason for c in out] == ["강의실"]
    assert "A" in str(out[0]) and "월 16:00~18:00" in str(out[0])
    # 같은 강사, 다른 강의실
    out = svc.schedule_conflicts(
        db, weekday=0, start=time(15), end=time(16, 30), room="2강", teacher_id=t.id
    )
    assert [c.reason for c in out] == ["강사"]
    # 맞닿기만 하면 겹침 아님, 다른 요일도 아님
    assert not svc.schedule_conflicts(
        db, weekday=0, start=time(18), end=time(19), room="1강", teacher_id=t.id
    )
    assert not svc.schedule_conflicts(
        db, weekday=1, start=time(16), end=time(18), room="1강", teacher_id=t.id
    )


def test_session_conflicts_ignore_cancelled(db):
    _class(db, room="1강")
    svc.generate_sessions(db, MON, weeks=1)
    db.commit()
    kw = dict(day=MON, start=time(17), end=time(18), room="1강", teacher_id=None)
    assert len(svc.session_conflicts(db, **kw)) == 1
    s = _sessions(db)[0]
    s.status = SessionStatus.cancelled
    db.commit()
    assert svc.session_conflicts(db, **kw) == []
