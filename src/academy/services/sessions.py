"""정규 시간표(class_schedules)로 실제 회차(sessions)를 만들고, 시간 겹침을 검사한다.

- 회차는 (반, 날짜, 시작 시각)으로 식별한다. 이미 있으면(휴강 포함) 다시 만들지 않는다.
- 시간표·반 정보가 바뀌면 '아직 손대지 않은 미래 회차'만 따라 바뀐다.
  출결이 기록됐거나 휴강·보강으로 바꾼 회차는 그대로 둔다.
"""

from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlmodel import Session, col, select

from academy.models import (
    Attendance,
    ClassGroup,
    ClassSchedule,
    ClassSession,
    SessionStatus,
    User,
)

WEEKDAYS = "월화수목금토일"
DEFAULT_WEEKS = 4
# 반별 색상 (목록 점, 캘린더). 반 id로 순환한다.
PALETTE = ["#3182f6", "#03b26c", "#fe9800", "#8b5cf6", "#f04452", "#18a5a7", "#f25c9a", "#5b6cf9"]


def class_color(class_id: int) -> str:
    return PALETTE[class_id % len(PALETTE)]


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


def fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


# --- 회차 생성 ----------------------------------------------------------------


def generate_sessions(
    db: Session, start: date, weeks: int = DEFAULT_WEEKS, class_id: int | None = None
) -> int:
    """start부터 weeks주치 회차를 만든다. 생성 개수를 반환. 커밋은 호출자가 한다."""
    end = start + timedelta(weeks=weeks)
    stmt = (
        select(ClassSchedule, ClassGroup)
        .join(ClassGroup, ClassGroup.id == ClassSchedule.class_id)
        .where(ClassGroup.active)
    )
    if class_id is not None:
        stmt = stmt.where(ClassGroup.id == class_id)
    pairs = db.exec(stmt).all()
    if not pairs:
        return 0

    existing = {
        (s.class_id, s.date, s.start_time)
        for s in db.exec(
            select(ClassSession).where(
                ClassSession.date >= start,
                ClassSession.date < end,
                col(ClassSession.class_id).in_({c.id for _, c in pairs}),
            )
        ).all()
    }
    created = 0
    for sched, cls in pairs:
        day = start + timedelta(days=(sched.weekday - start.weekday()) % 7)
        while day < end:
            key = (cls.id, day, sched.start_time)
            if key not in existing:
                db.add(
                    ClassSession(
                        class_id=cls.id,
                        schedule_id=sched.id,
                        date=day,
                        start_time=sched.start_time,
                        end_time=sched.end_time,
                        room=cls.room,
                        teacher_id=cls.teacher_id,
                    )
                )
                existing.add(key)
                created += 1
            day += timedelta(days=7)
    return created


def _untouched_future(db: Session, today: date):
    """자동 생성된 뒤 손대지 않은 미래 회차: 정상 상태이고 출결 기록이 없는 것."""
    return (
        select(ClassSession)
        .where(ClassSession.date >= today)
        .where(ClassSession.status == SessionStatus.normal)
        .where(col(ClassSession.schedule_id).is_not(None))
        .where(col(ClassSession.id).not_in(select(Attendance.session_id)))
    )


def remove_future_sessions(
    db: Session,
    today: date,
    *,
    schedule_id: int | None = None,
    class_id: int | None = None,
) -> int:
    """시간표 삭제·반 비활성화 시 미래 회차를 지운다."""
    stmt = _untouched_future(db, today)
    if schedule_id is not None:
        stmt = stmt.where(ClassSession.schedule_id == schedule_id)
    if class_id is not None:
        stmt = stmt.where(ClassSession.class_id == class_id)
    rows = db.exec(stmt).all()
    for s in rows:
        db.delete(s)
    return len(rows)


def sync_future_sessions(
    db: Session, cls: ClassGroup, today: date, *, old_room: str | None, old_teacher_id: int | None
) -> int:
    """반의 강의실·강사가 바뀌면 이전 값 그대로인 미래 회차를 새 값으로 바꾼다 (개별 대강은 유지)."""
    changed = 0
    for s in db.exec(_untouched_future(db, today).where(ClassSession.class_id == cls.id)).all():
        touched = False
        if s.room == old_room and s.room != cls.room:
            s.room, touched = cls.room, True
        if s.teacher_id == old_teacher_id and s.teacher_id != cls.teacher_id:
            s.teacher_id, touched = cls.teacher_id, True
        if touched:
            db.add(s)
            changed += 1
    return changed


# --- 겹침 검사 -----------------------------------------------------------------


@dataclass
class Conflict:
    reason: str  # "강의실" | "강사"
    class_name: str
    when: str  # "월 16:00~18:00" 또는 "11월 3일 16:00~18:00"

    def __str__(self) -> str:
        return f"{self.reason} 겹침 · {self.class_name} ({self.when})"


def _reasons(room, teacher_id, other_room, other_teacher_id) -> list[str]:
    reasons = []
    if room and room == other_room:
        reasons.append("강의실")
    if teacher_id and teacher_id == other_teacher_id:
        reasons.append("강사")
    return reasons


def schedule_conflicts(
    db: Session,
    *,
    weekday: int,
    start: time,
    end: time,
    room: str | None,
    teacher_id: int | None,
    exclude_class_id: int | None = None,
) -> list[Conflict]:
    """정규 시간표 기준 겹침. 다른 활성 반의 같은 요일 시간표와 강의실·강사가 겹치는지."""
    if not room and not teacher_id:
        return []
    stmt = (
        select(ClassSchedule, ClassGroup)
        .join(ClassGroup, ClassGroup.id == ClassSchedule.class_id)
        .where(ClassGroup.active, ClassSchedule.weekday == weekday)
    )
    if exclude_class_id is not None:
        stmt = stmt.where(ClassGroup.id != exclude_class_id)
    out = []
    for sched, cls in db.exec(stmt).all():
        if not overlaps(start, end, sched.start_time, sched.end_time):
            continue
        when = f"{WEEKDAYS[weekday]} {fmt_time(sched.start_time)}~{fmt_time(sched.end_time)}"
        for r in _reasons(room, teacher_id, cls.room, cls.teacher_id):
            out.append(Conflict(r, cls.name, when))
    return out


def class_schedule_conflicts(db: Session, cls: ClassGroup) -> list[Conflict]:
    """반의 강의실·강사를 바꿀 때: 이 반의 모든 시간표에 대해 겹침 검사."""
    out = []
    for sched in db.exec(select(ClassSchedule).where(ClassSchedule.class_id == cls.id)).all():
        out += schedule_conflicts(
            db,
            weekday=sched.weekday,
            start=sched.start_time,
            end=sched.end_time,
            room=cls.room,
            teacher_id=cls.teacher_id,
            exclude_class_id=cls.id,
        )
    return out


def session_conflicts(
    db: Session,
    *,
    day: date,
    start: time,
    end: time,
    room: str | None,
    teacher_id: int | None,
    exclude_session_id: int | None = None,
) -> list[Conflict]:
    """실제 회차 기준 겹침 (보강·대강·시간 변경). 휴강 회차는 제외."""
    if not room and not teacher_id:
        return []
    stmt = (
        select(ClassSession, ClassGroup)
        .join(ClassGroup, ClassGroup.id == ClassSession.class_id)
        .where(ClassSession.date == day, ClassSession.status != SessionStatus.cancelled)
    )
    if exclude_session_id is not None:
        stmt = stmt.where(ClassSession.id != exclude_session_id)
    out = []
    for s, cls in db.exec(stmt).all():
        if not overlaps(start, end, s.start_time, s.end_time):
            continue
        when = f"{day.month}월 {day.day}일 {fmt_time(s.start_time)}~{fmt_time(s.end_time)}"
        for r in _reasons(room, teacher_id, s.room, s.teacher_id):
            out.append(Conflict(r, cls.name, when))
    return out


def teacher_names(db: Session) -> dict[int, str]:
    return {u.id: u.name for u in db.exec(select(User)).all()}
