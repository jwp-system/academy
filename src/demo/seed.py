"""데모 데이터: 오늘 날짜를 기준으로 '운영 중인 학원'처럼 보이게 채운다. 모든 이름·연락처는 가짜.

reset()은 DB를 통째로 비우고 다시 채운다. 실수로 실제 DB를 지우지 않도록
파일 이름이 demo로 시작하는 SQLite에서만 동작한다.
"""

import logging
import random
from datetime import date, datetime, time, timedelta

from sqlmodel import Session, SQLModel, select

from app import edition
from app.auth import hash_password
from app.db import engine
from app.models import (
    AttendanceStatus,
    ClassGroup,
    ClassSchedule,
    ClassSession,
    Consultation,
    Enrollment,
    Guardian,
    Invoice,
    PaymentMethod,
    Role,
    SessionStatus,
    TxSource,
    User,
)
from app.schemas import StudentIn
from app.services import attendance, billing, matching
from app.services.backup import db_path
from app.services.notify import events, outbox
from app.services.sessions import generate_sessions
from app.services.students import create_student

log = logging.getLogger(__name__)

PASSWORD = "demo1234"
ACCOUNTS = {  # 역할: (이메일, 이름, 과목)
    "admin": ("admin@demo.academy", "김원장", None),
    "teacher": ("math@demo.academy", "이수학", "수학"),
    "teacher2": ("english@demo.academy", "박영어", "영어"),
}
CLASSES = [
    # 이름, 과목, 강사, 강의실, 정원, 수강료, [(요일, 시작, 끝)]
    ("중1 수학 기초", "수학", "teacher", "1강의실", 10, 250000, [(0, 15, 17), (2, 15, 17)]),
    ("중2 수학 A", "수학", "teacher", "1강의실", 10, 280000, [(0, 17, 19), (2, 17, 19)]),
    ("중3 수학 심화", "수학", "teacher", "1강의실", 8, 320000, [(1, 18, 20), (3, 18, 20)]),
    ("중2 영어 독해", "영어", "teacher2", "2강의실", 10, 260000, [(1, 16, 18), (3, 16, 18)]),
    ("고1 영어 내신", "영어", "teacher2", "2강의실", 8, 300000, [(4, 17, 20)]),
]
SURNAMES = "김이박최정강조윤장임한오서신권황안송류홍"
GIVEN = [
    "민준",
    "서연",
    "도윤",
    "하은",
    "시우",
    "지유",
    "주원",
    "서윤",
    "예준",
    "채원",
    "지호",
    "수아",
    "건우",
    "지민",
    "우진",
    "다은",
    "선우",
    "예린",
    "현우",
    "소율",
    "유준",
    "하린",
    "준서",
    "윤서",
]
SCHOOLS = [("한빛중", "중"), ("새솔중", "중"), ("푸른고", "고")]


def _students(rng: random.Random, db: Session) -> list:
    out = []
    for i, given in enumerate(GIVEN):
        surname = SURNAMES[i % len(SURNAMES)]
        school, level = SCHOOLS[i % 3]
        grade = f"{level}{1 + i % 3}"
        s = create_student(
            db,
            StudentIn(
                name=surname + given, school=school, grade=grade, phone=f"0100000{1000 + i:04d}"
            ),
        )
        db.flush()
        mom = Guardian(
            student_id=s.id,
            name=surname + "어머니",
            relation="어머니",
            phone=f"0100000{2000 + i:04d}",
            notify_sms=True,
        )
        if i % 4 == 0:
            mom.email, mom.notify_email = f"parent{i}@example.com", True
        db.add(mom)
        if i % 5 == 0:
            db.add(
                Guardian(
                    student_id=s.id,
                    name=surname + "아버지",
                    relation="아버지",
                    phone=f"0100000{3000 + i:04d}",
                    notify_sms=False,
                )
            )
        if i == len(GIVEN) - 1:
            db.add(
                Consultation(
                    student_id=s.id, content="방학 특강 문의. 고1 영어 내신반 추가 수강 희망."
                )
            )
        out.append(s)
    return out


def seed(db: Session, today: date | None = None) -> None:
    today = today or date.today()
    rng = random.Random(20261101)
    pw = hash_password(PASSWORD)

    users = {}
    for role, (email, name, subject) in ACCOUNTS.items():
        u = User(
            name=name,
            email=email,
            password_hash=pw,
            subject=subject,
            role=Role.admin if role == "admin" else Role.teacher,
        )
        db.add(u)
        users[role] = u
    db.flush()

    students = _students(rng, db)
    month = today.replace(day=1)
    last_month = billing.shift_month(month, -1)
    start = billing.shift_month(month, -2)

    classes = []
    for name, subject, t, room, cap, fee, slots in CLASSES:
        c = ClassGroup(
            name=name,
            subject=subject,
            teacher_id=users[t].id,
            room=room,
            capacity=cap,
            monthly_fee=fee,
        )
        db.add(c)
        db.flush()
        for wd, s, e in slots:
            db.add(ClassSchedule(class_id=c.id, weekday=wd, start_time=time(s), end_time=time(e)))
        classes.append(c)
    db.flush()

    # 수강: 학생마다 1~2개 반 (같은 학년대 위주)
    for i, s in enumerate(students):
        picks = [classes[i % 3]] + ([classes[3 + i % 2]] if i % 3 != 2 else [])
        for c in picks:
            db.add(Enrollment(student_id=s.id, class_id=c.id, start_date=start))
    db.flush()

    # 회차: 6주 전부터 4주 뒤까지, 지난주 하루 휴강 + 보강
    monday = today - timedelta(days=today.weekday())
    generate_sessions(db, monday - timedelta(weeks=6), weeks=10)
    db.flush()
    cancelled = db.exec(
        select(ClassSession)
        .where(ClassSession.class_id == classes[2].id, ClassSession.date < monday)
        .order_by(ClassSession.date.desc())
    ).first()
    if cancelled:
        cancelled.status, cancelled.note = SessionStatus.cancelled, "학교 시험 기간"
        db.add(
            ClassSession(
                class_id=classes[2].id,
                date=cancelled.date + timedelta(days=2),
                start_time=time(14),
                end_time=time(16),
                room="3강의실",
                teacher_id=users["teacher"].id,
                status=SessionStatus.makeup,
                note="시험 기간 보강",
            )
        )
    db.flush()

    # 주말처럼 오늘 수업이 없으면 특강을 넣어 출결 체크를 바로 해 볼 수 있게
    if not db.exec(
        select(ClassSession).where(
            ClassSession.date == today, ClassSession.status != SessionStatus.cancelled
        )
    ).first():
        for c, (h1, h2) in ((classes[1], (10, 12)), (classes[3], (14, 16))):
            db.add(
                ClassSession(
                    class_id=c.id,
                    date=today,
                    start_time=time(h1),
                    end_time=time(h2),
                    room=c.room,
                    teacher_id=c.teacher_id,
                    status=SessionStatus.makeup,
                    note="주말 특강",
                )
            )
        db.flush()

    # 출결: 지난 회차는 거의 다, 오늘 이미 시작한 회차는 절반쯤 체크
    now = datetime.now()
    past = db.exec(
        select(ClassSession).where(
            ClassSession.date <= today, ClassSession.status != SessionStatus.cancelled
        )
    ).all()
    for sess in past:
        started = datetime.combine(sess.date, sess.start_time) <= now
        if not started:
            continue
        rows = attendance.roster(db, sess)
        if sess.date == today:
            rows = rows[: len(rows) // 2]
        for r in rows:
            st = rng.choices(["present", "late", "absent", "excused", None], [84, 7, 5, 2, 2])[0]
            if st:
                attendance.set_status(
                    db,
                    sess,
                    r.student.id,
                    AttendanceStatus(st),
                    memo="병원 진료" if st == "excused" else None,
                )
    db.flush()

    # 수납: 지난달은 거의 완납, 이번 달은 섞어서
    billing.generate_invoices(db, last_month, 10)
    billing.generate_invoices(db, month, 10)
    db.flush()
    methods = [
        PaymentMethod.transfer,
        PaymentMethod.transfer,
        PaymentMethod.card,
        PaymentMethod.cash,
    ]
    for inv in db.exec(select(Invoice).where(Invoice.year_month == billing.ym(last_month))).all():
        if rng.random() < 0.9:
            p = billing.add_payment(
                db,
                inv,
                amount=billing.remaining(db, inv),
                method=rng.choice(methods),
                paid_at=datetime.combine(
                    inv.due_date - timedelta(days=rng.randint(0, 8)), time(rng.randint(9, 20))
                ),
            )
            p.cash_receipt_issued = p.method != PaymentMethod.card and rng.random() < 0.7
    this_month = db.exec(select(Invoice).where(Invoice.year_month == billing.ym(month))).all()
    for k, inv in enumerate(this_month):
        r = rng.random()
        paid_day = min(today, month + timedelta(days=rng.randint(0, 9)))
        if k < 4:  # 입금 문자로 자동 확인된 것처럼
            s = next(x for x in students if x.id == inv.student_id)
            matching.ingest(
                db,
                received_at=datetime.combine(paid_day, time(10, k)),
                payer_name=s.payer_code,
                amount=billing.remaining(db, inv),
                source=TxSource.sms,
                raw_text="(데모) 입금 알림",
            )
        elif r < 0.45:
            billing.add_payment(
                db,
                inv,
                amount=billing.remaining(db, inv),
                method=rng.choice(methods),
                paid_at=datetime.combine(paid_day, time(rng.randint(9, 20))),
            )
        elif r < 0.6:
            billing.add_payment(
                db,
                inv,
                amount=100000,
                method=PaymentMethod.transfer,
                paid_at=datetime.combine(paid_day, time(11)),
                memo="1차",
            )
    # 확인이 필요한 입금
    unpaid = [i for i in this_month if i.status != "paid"]
    if unpaid:
        s = next(x for x in students if x.id == unpaid[0].student_id)
        matching.ingest(
            db,
            received_at=datetime.combine(today, time(9, 12)),
            payer_name=s.name + "엄마",
            amount=150000,
            source=TxSource.sms,
        )
    matching.ingest(
        db,
        received_at=datetime.combine(today, time(9, 30)),
        payer_name="모르는입금",
        amount=50000,
        source=TxSource.sms,
    )
    db.flush()

    # 알림: 청구 안내·리마인드·미납 안내를 테스트 수신함으로 보내 두고, 실패 하나
    events.send_invoice_notices(db, month)
    events.send_payment_reminders(db, today)
    db.flush()
    outbox.process_pending(db, test_flush=True)
    failed = outbox.enqueue(
        db,
        to="01000009999",
        template="absence",
        body="[데모] 발송 실패 예시 문자예요.",
        dedupe=False,
    )
    failed.status, failed.attempts, failed.error, failed.provider = (
        "failed",
        3,
        "테스트 모드: 발송 실패 흉내",
        "test",
    )
    db.add(failed)
    db.commit()
    log.info("데모 데이터를 채웠어요 (학생 %d명)", len(students))


def _guard() -> None:
    path = db_path()
    if not path.name.startswith("demo"):
        raise RuntimeError(f"데모 초기화는 demo*.db 파일에서만 할 수 있어요: {path}")


def reset() -> None:
    """DB를 비우고 다시 채운다 (매일 새벽 4시, 또는 로그인 화면의 '처음 상태로')."""
    from demo.edition import build  # edition → seed 순환이라 여기서

    _guard()
    edition.activate(build())  # 명령줄에서 불러도 실제 발송이 꺼진 상태로
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        seed(db)


def ensure_seeded() -> None:
    """앱 시작 시: 표가 없으면 만들고, 비어 있으면 채운다."""
    _guard()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        if db.exec(select(User.id)).first() is None:
            seed(db)
