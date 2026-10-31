from datetime import datetime, timedelta

from sqlmodel import select

from app.models import Channel, Notification, NotificationStatus, Role, User
from app.services.notify import channels, outbox
from app.services.notify.outbox import next_allowed_time

NOON = datetime(2026, 11, 2, 12, 0)


def test_quiet_hours_deferred_to_morning():
    assert next_allowed_time(datetime(2026, 11, 2, 22, 30)) == datetime(2026, 11, 3, 8, 0)
    assert next_allowed_time(datetime(2026, 11, 2, 6, 0)) == datetime(2026, 11, 2, 8, 0)
    assert next_allowed_time(NOON) == NOON


def test_tests_never_send_for_real():
    assert channels.mode(Channel.sms) == "test" and channels.mode(Channel.email) == "test"
    assert channels.adapter_for(Channel.sms) is channels.TEST


def test_sent_in_test_mode_records_provider(db):
    n = outbox.enqueue(db, to="01011112222", template="absence", body="x", scheduled_at=NOON)
    db.commit()
    assert outbox.process_pending(db, now=NOON) == 1
    db.refresh(n)
    assert n.status == "sent" and n.provider == "test" and n.sent_at


def test_not_sent_before_schedule_or_at_night(db):
    outbox.enqueue(db, to="010", template="absence", body="x", scheduled_at=NOON)
    db.commit()
    assert outbox.process_pending(db, now=NOON - timedelta(minutes=1)) == 0
    assert outbox.process_pending(db, now=datetime(2026, 11, 2, 23, 0)) == 0


def test_dedupe_same_student_type_day_and_ref(db):
    from app.schemas import StudentIn
    from app.services.students import create_student

    s = create_student(db, StudentIn(name="가"))
    db.flush()
    kw = dict(to="010", template="absence", body="x", student_id=s.id, scheduled_at=NOON)
    assert outbox.enqueue(db, **kw, ref="absence:1")
    assert outbox.enqueue(db, **kw, ref="absence:2") is None  # 같은 날 같은 유형 1회
    assert outbox.enqueue(db, **kw | {"to": "011"}, ref="absence:2")  # 다른 보호자는 따로
    kw2 = kw | {"scheduled_at": NOON + timedelta(days=1)}
    assert outbox.enqueue(db, **kw2, ref="absence:1") is None  # ref 중복
    assert outbox.enqueue(db, **kw2, ref="absence:3")  # 다음 날은 다시


def test_cancelled_does_not_block(db):
    n = outbox.enqueue(db, to="010", template="t", body="x", ref="r", scheduled_at=NOON)
    assert outbox.cancel(db, ref="r") == 1
    assert n.status == "cancelled"
    assert outbox.enqueue(db, to="010", template="t", body="x", ref="r", scheduled_at=NOON)


def test_retry_then_failed_and_gateway_alert(db):
    db.add(User(name="원장", email="boss@x.com", password_hash="h", role=Role.admin))
    channels.TEST.fail = True
    for i in range(3):
        outbox.enqueue(db, to=f"010{i}", template="absence", body="x", scheduled_at=NOON)
    db.commit()
    for _ in range(3):
        outbox.process_pending(db, now=NOON)
    sms = db.exec(select(Notification).where(Notification.channel == "sms")).all()
    assert all(n.status == "failed" and n.attempts == 3 for n in sms)
    alerts = db.exec(select(Notification).where(Notification.template == "gateway_down")).all()
    assert len(alerts) == 1 and alerts[0].to == "boss@x.com"  # 하루 한 번만

    channels.TEST.fail = False
    outbox.retry(db, sms[0])
    db.commit()
    outbox.process_pending(db, now=datetime.now().replace(hour=12))
    db.refresh(sms[0])
    assert sms[0].status == NotificationStatus.sent


def test_test_flush_ignores_schedule(db):
    later = datetime.now() + timedelta(hours=3)
    outbox.enqueue(db, to="010", template="absence", body="x", scheduled_at=later)
    db.commit()
    assert outbox.process_pending(db, test_flush=True) == 1
