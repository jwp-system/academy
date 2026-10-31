"""출결 (4주차).

- 회차의 출석부 = 그날 수강 중인 학생(+ 이미 출결 기록이 있는 학생).
- 출결 기록이 없으면 '미체크'. 미체크로 되돌리면 기록을 지운다.
- 탭할 때마다 출석 → 지각 → 결석 → 출석 순으로 바뀐다. 공결은 메모 창에서 고른다.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement
from sqlmodel import Session, col, or_, select

from app.models import (
    Attendance,
    AttendanceStatus,
    ClassGroup,
    ClassSession,
    Enrollment,
    Role,
    SessionStatus,
    Student,
    User,
)
from app.services.notify import events

LABELS = {
    None: "미체크",
    AttendanceStatus.present: "출석",
    AttendanceStatus.late: "지각",
    AttendanceStatus.absent: "결석",
    AttendanceStatus.excused: "공결",
}

_CYCLE = {
    None: AttendanceStatus.present,
    AttendanceStatus.present: AttendanceStatus.late,
    AttendanceStatus.late: AttendanceStatus.absent,
    AttendanceStatus.absent: AttendanceStatus.present,
    AttendanceStatus.excused: AttendanceStatus.present,
}


KEEP = object()  # set_status(memo=KEEP): 메모를 바꾸지 않음


def next_status(current: AttendanceStatus | None) -> AttendanceStatus:
    return _CYCLE[current]


def enrolled_on(day: date) -> ColumnElement[bool]:
    """그날 수강 중인 수강 기록 조건."""
    return (col(Enrollment.start_date) <= day) & or_(
        col(Enrollment.end_date).is_(None), col(Enrollment.end_date) >= day
    )


def can_check(session: ClassSession, today: date | None = None) -> bool:
    """휴강이 아니고, 수업 당일이거나 지난 회차만 체크할 수 있다."""
    return session.status != SessionStatus.cancelled and session.date <= (today or date.today())


def can_access(db: Session, user: User, session: ClassSession) -> bool:
    if user.role == Role.admin or session.teacher_id == user.id:
        return True
    cls = db.get(ClassGroup, session.class_id)
    return cls is not None and cls.teacher_id == user.id


def sessions_on(db: Session, user: User, day: date) -> list[tuple[ClassSession, ClassGroup]]:
    """그날 회차 목록. 강사는 본인이 맡은 회차(대강 포함)와 담당 반 회차만."""
    stmt = (
        select(ClassSession, ClassGroup)
        .join(ClassGroup, ClassGroup.id == ClassSession.class_id)
        .where(ClassSession.date == day)
        .order_by(ClassSession.start_time, ClassGroup.name)
    )
    if user.role != Role.admin:
        stmt = stmt.where(or_(ClassSession.teacher_id == user.id, ClassGroup.teacher_id == user.id))
    return list(db.exec(stmt).all())


@dataclass
class RosterRow:
    student: Student
    attendance: Attendance | None

    @property
    def status(self) -> AttendanceStatus | None:
        return self.attendance.status if self.attendance else None


def roster(db: Session, session: ClassSession) -> list[RosterRow]:
    records = {
        a.student_id: a
        for a in db.exec(select(Attendance).where(Attendance.session_id == session.id)).all()
    }
    enrolled = select(Enrollment.student_id).where(
        Enrollment.class_id == session.class_id, enrolled_on(session.date)
    )
    students = db.exec(
        select(Student)
        .where(or_(col(Student.id).in_(enrolled), col(Student.id).in_(list(records))))
        .order_by(Student.name)
    ).all()
    return [RosterRow(s, records.get(s.id)) for s in students]


def roster_ids(db: Session, session: ClassSession) -> set[int]:
    return {r.student.id for r in roster(db, session)}


def set_status(
    db: Session,
    session: ClassSession,
    student_id: int,
    status: AttendanceStatus | None,
    memo: str | None | object = KEEP,
) -> Attendance | None:
    """출결을 기록한다. status=None이면 미체크로 되돌린다(메모도 함께 지워짐). 커밋은 호출자가 한다."""
    a = db.exec(
        select(Attendance).where(
            Attendance.session_id == session.id, Attendance.student_id == student_id
        )
    ).first()
    if status is None:
        if a:
            db.delete(a)
        on_attendance_changed(db, session, student_id, None)
        return None
    if a is None:
        a = Attendance(session_id=session.id, student_id=student_id, status=status)
    a.status = status
    if memo is not KEEP:
        a.memo = memo
    db.add(a)
    on_attendance_changed(db, session, student_id, status)
    return a


def on_attendance_changed(
    db: Session, session: ClassSession, student_id: int, status: AttendanceStatus | None
) -> None:
    """결석이면 수업 시작 10분 뒤 결석 알림을 예약하고, 결석이 아니게 되면 아직 안 나간 예약을 취소한다."""
    if status == AttendanceStatus.absent:
        events.schedule_absence(db, session, student_id)
    else:
        events.cancel_absence(db, session, student_id)


@dataclass
class Counts:
    present: int = 0
    late: int = 0
    absent: int = 0
    excused: int = 0
    unchecked: int = 0

    @property
    def total(self) -> int:
        return self.present + self.late + self.absent + self.excused + self.unchecked

    @property
    def checked(self) -> int:
        return self.total - self.unchecked

    @property
    def rate(self) -> int | None:
        """출석률(%) = (출석+지각) / (체크한 수 - 공결). 셀 대상이 없으면 None."""
        base = self.checked - self.excused
        return round((self.present + self.late) * 100 / base) if base else None

    def add(self, status: AttendanceStatus | None) -> None:
        name = AttendanceStatus(status).value if status else "unchecked"
        setattr(self, name, getattr(self, name) + 1)


def count(statuses) -> Counts:
    c = Counts()
    for s in statuses:
        c.add(s)
    return c


def day_counts(db: Session, sessions: list[ClassSession]) -> dict[int, Counts]:
    return {s.id: count(r.status for r in roster(db, s)) for s in sessions}


def is_ongoing(session: ClassSession, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return session.date == now.date() and session.start_time <= now.time() < session.end_time


# --- 학생별 출결 이력 --------------------------------------------------------------


@dataclass
class HistoryRow:
    session: ClassSession
    cls: ClassGroup
    attendance: Attendance | None

    @property
    def status(self) -> AttendanceStatus | None:
        return self.attendance.status if self.attendance else None


@dataclass
class History:
    month: date  # 그 달 1일
    rows: list[HistoryRow] = field(default_factory=list)

    @property
    def counts(self) -> Counts:
        return count(r.status for r in self.rows)

    @property
    def prev_month(self) -> date:
        return (self.month - timedelta(days=1)).replace(day=1)

    @property
    def next_month(self) -> date:
        return (self.month + timedelta(days=32)).replace(day=1)


def student_history(
    db: Session, student: Student, month: date, today: date | None = None
) -> History:
    """그 달에 학생이 들어야 했던 회차(휴강 제외, 오늘까지)와 출결. 기록이 없으면 미체크."""
    today = today or date.today()
    start = month.replace(day=1)
    end = min((start + timedelta(days=32)).replace(day=1) - timedelta(days=1), today)
    records = {
        a.session_id: a
        for a in db.exec(select(Attendance).where(Attendance.student_id == student.id)).all()
    }
    enrollments = db.exec(select(Enrollment).where(Enrollment.student_id == student.id)).all()
    sessions = db.exec(
        select(ClassSession, ClassGroup)
        .join(ClassGroup, ClassGroup.id == ClassSession.class_id)
        .where(
            ClassSession.date >= start,
            ClassSession.date <= end,
            ClassSession.status != SessionStatus.cancelled,
            or_(
                col(ClassSession.class_id).in_({e.class_id for e in enrollments}),
                col(ClassSession.id).in_(list(records)),
            ),
        )
        .order_by(col(ClassSession.date).desc(), col(ClassSession.start_time).desc())
    ).all()

    def enrolled(s: ClassSession) -> bool:
        return any(
            e.class_id == s.class_id
            and e.start_date <= s.date
            and (e.end_date is None or e.end_date >= s.date)
            for e in enrollments
        )

    rows = [
        HistoryRow(s, c, records.get(s.id)) for s, c in sessions if s.id in records or enrolled(s)
    ]
    return History(month=start, rows=rows)
