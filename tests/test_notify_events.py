import json
from datetime import date, datetime, time, timedelta

import pytest
from sqlmodel import select

from app.models import (
    AttendanceStatus,
    ClassGroup,
    ClassSession,
    Enrollment,
    Guardian,
    Invoice,
    Notification,
    PaymentMethod,
)
from app.schemas import StudentIn
from app.services import attendance, billing
from app.services.notify import events
from app.services.notify import templates as tpl
from app.services.students import create_student


def _student(db, name="홍길동", phones=("01011112222",), emails=()):
    s = create_student(db, StudentIn(name=name))
    db.flush()
    for p in phones:
        db.add(Guardian(student_id=s.id, name="엄마", phone=p, notify_sms=True))
    db.add(Guardian(student_id=s.id, name="아빠", phone="01099990000", notify_sms=False))
    for e in emails:
        db.add(Guardian(student_id=s.id, name="메일", email=e, notify_email=True))
    db.commit()
    return s


def _notes(db, template=None):
    stmt = select(Notification).order_by(Notification.id)
    if template:
        stmt = stmt.where(Notification.template == template)
    return db.exec(stmt).all()


# --- 템플릿 ------------------------------------------------------------------


def test_template_validate_render_and_override(db):
    assert tpl.validate("absence", "{학생명} {없는값}") == "쓸 수 없는 자리표시자예요: {없는값}"
    assert tpl.validate("absence", "  ") == "내용을 입력해 주세요."
    body = tpl.render(
        db, "absence", {"학생명": "가", "반명": "수학", "날짜": "1일", "시작시각": "16:00"}
    )
    assert body.startswith("[테스트학원] 가 학생이 오늘 16:00 수학 수업에 결석했어요")
    tpl.save(db, "absence", "{학생명} 결석")
    db.commit()
    assert tpl.is_custom(db, "absence") and tpl.render(db, "absence", {"학생명": "가"}) == "가 결석"
    tpl.save(db, "absence", tpl.SPECS["absence"].default)  # 기본값과 같으면 저장 안 함
    db.commit()
    assert not tpl.is_custom(db, "absence")
    assert tpl.sms_bytes("가a") == 3


# --- 결석 -------------------------------------------------------------------


@pytest.fixture
def today_session(db):
    c = ClassGroup(name="중2 수학")
    db.add(c)
    db.flush()
    s = ClassSession(class_id=c.id, date=date.today(), start_time=time(16), end_time=time(18))
    db.add(s)
    db.commit()
    return s


def test_absence_scheduled_10min_after_start_and_cancelled(db, today_session):
    st = _student(db, phones=("01011112222", "01033334444"))
    db.add(Enrollment(student_id=st.id, class_id=today_session.class_id, start_date=date.today()))
    db.commit()
    attendance.set_status(db, today_session, st.id, AttendanceStatus.absent)
    db.commit()
    notes = _notes(db, "absence")
    assert sorted(n.to for n in notes) == ["01011112222", "01033334444"]  # 문자 끈 아빠는 제외
    start = datetime.combine(date.today(), time(16))
    expect = outbox_time(max(datetime.now(), start + timedelta(minutes=10)))
    assert all(abs((n.scheduled_at - expect).total_seconds()) < 5 for n in notes)
    # 출석으로 바뀌면 취소
    attendance.set_status(db, today_session, st.id, AttendanceStatus.present)
    db.commit()
    assert {n.status for n in _notes(db, "absence")} == {"cancelled"}
    # 다시 결석 → 새로 예약
    attendance.set_status(db, today_session, st.id, AttendanceStatus.absent)
    db.commit()
    assert sum(n.status == "pending" for n in _notes(db, "absence")) == 2


def outbox_time(t):
    from app.services.notify.outbox import next_allowed_time

    return next_allowed_time(t)


def test_no_absence_alert_for_past_session(db, today_session):
    st = _student(db)
    today_session.date = date.today() - timedelta(days=1)
    db.commit()
    attendance.set_status(db, today_session, st.id, AttendanceStatus.absent)
    db.commit()
    assert _notes(db) == []


# --- 수납 -------------------------------------------------------------------


@pytest.fixture
def invoice(db):
    st = _student(db, emails=("mom@x.com",))
    c = ClassGroup(name="수학", monthly_fee=280000)
    db.add(c)
    db.flush()
    db.add(Enrollment(student_id=st.id, class_id=c.id, start_date=date(2026, 9, 1)))
    db.commit()
    billing.generate_invoices(db, date(2026, 11, 1), 10)
    db.commit()
    return db.exec(select(Invoice)).one()


def test_invoice_notice_sms_and_email_once(db, invoice):
    assert events.send_invoice_notices(db, date(2026, 11, 1)) == 2
    db.commit()
    sms, email = _notes(db, "invoice")
    assert sms.channel == "sms" and email.channel == "email" and email.to == "mom@x.com"
    assert (
        "280,000원" in sms.body
        and "11월 10일" in sms.body
        and "홍길동1" in sms.body
        and "OO은행 123-456" in sms.body
    )
    assert events.send_invoice_notices(db, date(2026, 11, 1)) == 0  # 두 번 보내지 않음


def test_paid_invoice_gets_no_notice(db, invoice):
    billing.mark_paid(db, invoice, PaymentMethod.card)
    db.commit()
    assert events.send_invoice_notices(db, date(2026, 11, 1)) == 0


def test_reminder_window(db, invoice):
    assert events.send_payment_reminders(db, date(2026, 11, 6)) == 0  # 4일 전
    assert events.send_payment_reminders(db, date(2026, 11, 7)) == 1  # 3일 전
    db.commit()
    assert events.send_payment_reminders(db, date(2026, 11, 8)) == 0  # 이미 보냄
    assert "280,000원" in _notes(db, "reminder")[0].body  # 남은 금액


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 11, 10), None),
        (date(2026, 11, 11), 0),
        (date(2026, 11, 17), 0),
        (date(2026, 11, 18), 1),
        (date(2026, 11, 25), 2),
        (date(2026, 12, 30), 2),
    ],
)
def test_overdue_rounds(invoice, day, expected):
    assert events.overdue_round(invoice, day) == expected


def test_overdue_sends_latest_round_once(db, invoice):
    assert events.send_payment_reminders(db, date(2026, 11, 11)) == 1
    db.commit()
    assert events.send_payment_reminders(db, date(2026, 11, 12)) == 0
    # 실제로는 11일에 나간 알림 (하루 1회 제한은 실제 발송일 기준)
    first = _notes(db, "overdue")[0]
    first.scheduled_at = datetime(2026, 11, 11, 10)
    db.commit()
    # PC가 꺼져 있다가 25일에 켜짐 → 밀린 회차를 몰아 보내지 않고 마지막 회차 하나만
    assert events.send_payment_reminders(db, date(2026, 11, 25)) == 1
    db.commit()
    assert [n.ref for n in _notes(db, "overdue")] == [
        f"invoice:{invoice.id}:overdue:0",
        f"invoice:{invoice.id}:overdue:2",
    ]


def test_run_daily_generates_on_first(db, invoice):
    db.delete(invoice)
    db.commit()
    out = events.run_daily(db, date(2026, 11, 1))
    assert out["invoices"] == 1 and out["invoice_notices"] == 2


# --- 화면 --------------------------------------------------------------------


@pytest.fixture
def admin(client, login):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return client


def _toast(resp):
    return json.loads(resp.headers["HX-Trigger"]).get("toast", "")


def test_pages_render(admin):
    plain = {"HX-Request": ""}
    for url in ["/notifications", "/notifications/templates", "/notifications/test"]:
        r = admin.get(url, headers=plain)
        assert r.status_code == 200 and "</html>" in r.text, url
    assert "알림 테스트 모드" in admin.get("/", headers=plain).text


def test_test_send_lands_in_inbox(admin, db):
    resp = admin.post(
        "/notifications/test/send", data={"channel": "sms", "to": "010-1234-5678", "body": "안녕"}
    )
    assert "테스트 수신함에 도착했어요" in _toast(resp)
    n = db.exec(select(Notification)).one()
    assert n.to == "01012345678" and n.provider == "test" and n.status == "sent"
    assert "안녕" in admin.get("/notifications/test").text
    resp = admin.post("/notifications/test/send", data={"channel": "sms", "to": "12", "body": ""})
    assert "전화번호 형식을 확인해 주세요." in resp.text and "내용을 입력해 주세요." in resp.text


def test_fail_toggle_retry_and_cancel(admin, db):
    admin.post("/notifications/test/fail", data={"fail": "on"})
    admin.post(
        "/notifications/test/send", data={"channel": "sms", "to": "01012345678", "body": "x"}
    )
    n = db.exec(select(Notification)).one()
    assert n.status == "failed" and "실패 흉내" in n.error
    admin.post("/notifications/test/fail", data={})
    admin.post(f"/notifications/{n.id}/retry")
    db.refresh(n)
    assert n.status == "pending"
    admin.post("/notifications/test/flush")
    db.refresh(n)
    assert n.status == "sent"
    assert admin.post(f"/notifications/{n.id}/cancel").status_code == 400


def test_template_edit_endpoints(admin, db):
    resp = admin.put("/notifications/templates/absence", data={"body": "{학생명} {틀림}"})
    assert "쓸 수 없는 자리표시자예요" in resp.text
    resp = admin.put("/notifications/templates/absence", data={"body": "{학생명} 결석했어요"})
    assert "저장했어요" in _toast(resp)
    assert tpl.get_body(db, "absence") == "{학생명} 결석했어요"
    admin.post("/notifications/templates/absence/reset")
    assert not tpl.is_custom(db, "absence")
    assert admin.put("/notifications/templates/nope", data={"body": "x"}).status_code == 404


def test_billing_notify_button(admin, db, invoice):
    page = admin.get("/billing?month=2026-11", headers={"HX-Request": ""}).text
    assert "청구 안내 문자를 아직 보내지 않은 청구가 1건" in page
    resp = admin.post("/billing/notify?month=2026-11")
    assert "청구 안내 2건" in _toast(resp)
    assert (
        "청구 안내 문자를 아직"
        not in admin.get("/billing?month=2026-11", headers={"HX-Request": ""}).text
    )


def test_teacher_cannot_use_notifications(client, login):
    from app.models import Role

    token = login(Role.teacher)
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    assert client.get("/notifications/test").status_code == 403
    assert (
        client.post("/notifications/test/send", data={"to": "010", "body": "x"}).status_code == 403
    )
