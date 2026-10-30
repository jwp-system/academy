import json
from datetime import date, time, timedelta

import pytest
from sqlmodel import select

from academy.models import (
    Attendance,
    AttendanceStatus,
    ClassGroup,
    ClassSession,
    Enrollment,
    Role,
    SessionStatus,
    User,
)
from academy.schemas import StudentIn
from academy.services import attendance as svc
from academy.services.students import create_student

TODAY = date.today()


def _setup(db, teacher_id=None, day=TODAY, names=("가나", "다라", "마바")):
    c = ClassGroup(name="중2 수학", teacher_id=teacher_id, room="1강")
    db.add(c)
    db.flush()
    s = ClassSession(
        class_id=c.id, date=day, start_time=time(16), end_time=time(18), teacher_id=teacher_id
    )
    db.add(s)
    students = []
    for n in names:
        st = create_student(db, StudentIn(name=n))
        db.flush()
        db.add(Enrollment(student_id=st.id, class_id=c.id, start_date=day - timedelta(days=30)))
        students.append(st)
    db.commit()
    return c, s, students


# --- 서비스 ------------------------------------------------------------------


def test_cycle():
    order = [None]
    for _ in range(4):
        order.append(svc.next_status(order[-1]))
    assert order == [None, "present", "late", "absent", "present"]
    assert svc.next_status(AttendanceStatus.excused) == "present"


def test_roster_respects_enrollment_dates(db):
    c, s, (a, b, d) = _setup(db)
    e = db.exec(select(Enrollment).where(Enrollment.student_id == b.id)).one()
    e.end_date = TODAY - timedelta(days=1)  # 어제까지 수강
    late = db.exec(select(Enrollment).where(Enrollment.student_id == d.id)).one()
    late.start_date = TODAY + timedelta(days=1)  # 내일부터 수강
    db.commit()
    assert [r.student.name for r in svc.roster(db, s)] == ["가나"]
    # 수강이 끝났어도 이미 기록이 있으면 출석부에 남는다
    db.add(Attendance(session_id=s.id, student_id=b.id, status=AttendanceStatus.present))
    db.commit()
    assert [r.student.name for r in svc.roster(db, s)] == ["가나", "다라"]


def test_set_status_none_deletes(db):
    _, s, (a, *_) = _setup(db)
    svc.set_status(db, s, a.id, AttendanceStatus.absent, memo="병결")
    db.commit()
    svc.set_status(db, s, a.id, AttendanceStatus.excused)  # 메모 유지
    db.commit()
    rec = db.exec(select(Attendance)).one()
    assert rec.status == "excused" and rec.memo == "병결"
    svc.set_status(db, s, a.id, None)
    db.commit()
    assert db.exec(select(Attendance)).first() is None


def test_counts_rate():
    c = svc.count(["present", "present", "late", "absent", "excused", None])
    assert (c.present, c.late, c.absent, c.excused, c.unchecked) == (2, 1, 1, 1, 1)
    assert c.rate == 75  # 3 / (5 - 1)
    assert svc.count([]).rate is None
    assert svc.count(["excused"]).rate is None


def test_student_history_includes_unchecked_and_skips_cancelled_future(db):
    c, s, (a, *_) = _setup(db)
    past = ClassSession(
        class_id=c.id, date=TODAY - timedelta(days=1), start_time=time(16), end_time=time(18)
    )
    cancelled = ClassSession(
        class_id=c.id,
        date=TODAY - timedelta(days=2),
        start_time=time(16),
        end_time=time(18),
        status=SessionStatus.cancelled,
    )
    future = ClassSession(
        class_id=c.id, date=TODAY + timedelta(days=1), start_time=time(16), end_time=time(18)
    )
    db.add_all([past, cancelled, future])
    db.commit()
    svc.set_status(db, s, a.id, AttendanceStatus.present)
    db.commit()
    h = svc.student_history(db, a, TODAY, today=TODAY)
    ids = [r.session.id for r in h.rows]
    assert s.id in ids and future.id not in ids and cancelled.id not in ids
    if past.date.month == TODAY.month:
        assert past.id in ids
        assert h.counts.unchecked == 1


# --- 화면 --------------------------------------------------------------------


@pytest.fixture
def teacher(client, login, db):
    token = login(Role.teacher)
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return db.exec(select(User).where(User.role == Role.teacher)).one()


def test_teacher_checks_own_session(client, teacher, db):
    _, s, (a, b, d) = _setup(db, teacher_id=teacher.id)
    day = client.get("/attendance", headers={"HX-Request": ""}).text
    assert "중2 수학" in day and "0/3명 체크" in day

    resp = client.post(f"/attendance/sessions/{s.id}/students/{a.id}", data={"status": "present"})
    assert resp.status_code == 200
    assert 'class="att-tile present"' in resp.text
    assert '"status": "late"' in resp.text  # 다음 탭은 지각
    assert 'id="att-counts"' in resp.text and 'hx-swap-oob="true"' in resp.text
    # 같은 값을 두 번 보내도 결과는 같다 (빠른 연타)
    client.post(f"/attendance/sessions/{s.id}/students/{a.id}", data={"status": "present"})
    assert len(db.exec(select(Attendance)).all()) == 1

    resp = client.post(f"/attendance/sessions/{s.id}/all-present")
    assert "2명을 출석으로 체크했어요" in json.loads(resp.headers["HX-Trigger"])["toast"]
    assert {r.status for r in db.exec(select(Attendance)).all()} == {"present"}


def test_memo_and_excused_via_edit(client, teacher, db):
    _, s, (a, *_) = _setup(db, teacher_id=teacher.id)
    assert client.get(f"/attendance/sessions/{s.id}/students/{a.id}/edit").status_code == 200
    resp = client.put(
        f"/attendance/sessions/{s.id}/students/{a.id}",
        data={"status": "excused", "memo": "학교 행사"},
    )
    assert "attendanceSaved" in json.loads(resp.headers["HX-Trigger"])
    rec = db.exec(select(Attendance)).one()
    assert rec.status == "excused" and rec.memo == "학교 행사"
    client.put(f"/attendance/sessions/{s.id}/students/{a.id}", data={"status": ""})
    assert db.exec(select(Attendance)).first() is None


def test_cannot_check_future_or_cancelled(client, teacher, db):
    _, future, (a, *_) = _setup(db, teacher_id=teacher.id, day=TODAY + timedelta(days=1))
    resp = client.post(
        f"/attendance/sessions/{future.id}/students/{a.id}", data={"status": "present"}
    )
    assert resp.status_code == 400
    page = client.get(f"/attendance/sessions/{future.id}", headers={"HX-Request": ""}).text
    assert "아직 수업 날이 아니에요" in page and "disabled" in page
    future.date = TODAY
    future.status = SessionStatus.cancelled
    db.commit()
    assert client.post(f"/attendance/sessions/{future.id}/all-present").status_code == 400


def test_rejects_non_roster_student_and_bad_status(client, teacher, db):
    _, s, _ = _setup(db, teacher_id=teacher.id)
    outsider = create_student(db, StudentIn(name="외부"))
    db.commit()
    assert (
        client.post(
            f"/attendance/sessions/{s.id}/students/{outsider.id}", data={"status": "present"}
        ).status_code
        == 404
    )
    st_id = db.exec(select(Enrollment)).first().student_id
    assert (
        client.post(
            f"/attendance/sessions/{s.id}/students/{st_id}", data={"status": "zzz"}
        ).status_code
        == 400
    )


def test_teacher_cannot_see_other_sessions(client, teacher, db):
    _, s, (a, *_) = _setup(db)  # 담당 강사 없음
    assert "중2 수학" not in client.get("/attendance").text
    assert client.get(f"/attendance/sessions/{s.id}").status_code == 404
    assert (
        client.post(
            f"/attendance/sessions/{s.id}/students/{a.id}", data={"status": "present"}
        ).status_code
        == 404
    )
    # 대강으로 배정되면 볼 수 있다
    s.teacher_id = teacher.id
    db.commit()
    assert client.get(f"/attendance/sessions/{s.id}").status_code == 200


def test_withdraw_ends_enrollment(client, login, db):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    _, s, (a, *_) = _setup(db, day=TODAY + timedelta(days=1))
    client.delete(f"/students/{a.id}")
    e = db.exec(select(Enrollment).where(Enrollment.student_id == a.id)).one()
    assert e.end_date == TODAY
    assert a.id not in svc.roster_ids(db, s)  # 내일 수업 출석부에서 빠짐


def test_student_attendance_history_partial(client, login, db):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    _, s, (a, *_) = _setup(db)
    svc.set_status(db, s, a.id, AttendanceStatus.late)
    db.commit()
    html = client.get(f"/students/{a.id}/attendance").text
    assert "지각" in html and "출석률" in html and "100%" in html
    prev = (TODAY.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    assert (
        "이 달에 들은 수업이 없어요" in client.get(f"/students/{a.id}/attendance?month={prev}").text
    )
    assert client.get(f"/students/{a.id}/attendance?month=2026-13").status_code == 400
    assert client.get(f"/students/{a.id}", headers={"HX-Request": ""}).status_code == 200
