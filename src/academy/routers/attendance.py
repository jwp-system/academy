"""출결 체크 (모바일 우선). 오늘 회차 목록 → 회차별 학생 버튼 그리드.

학생 버튼을 누르면 다음 상태를 서버에 보낸다(hx-vals). 같은 버튼을 빠르게 두 번 눌러도
같은 값이 두 번 저장될 뿐이라 상태가 꼬이지 않는다.
"""

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session

from academy.auth import CurrentUser
from academy.db import DBSession
from academy.models import AttendanceStatus, ClassGroup, ClassSession, Student, User
from academy.services import attendance as svc
from academy.services.sessions import WEEKDAYS, teacher_names
from academy.templating import FormData, hx_trigger, render, trigger_headers

router = APIRouter(prefix="/attendance")


def _parse_day(day: str | None) -> date:
    if not day:
        return date.today()
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜를 확인해 주세요.") from None


def _parse_status(value: str | None) -> AttendanceStatus | None:
    if not value:
        return None
    try:
        return AttendanceStatus(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="출결 상태를 확인해 주세요.") from None


def _get_session(db: Session, user: User, session_id: int) -> tuple[ClassSession, ClassGroup]:
    s = db.get(ClassSession, session_id)
    if s is None or not svc.can_access(db, user, s):
        raise HTTPException(status_code=404)
    return s, db.get(ClassGroup, s.class_id)


def _require_checkable(s: ClassSession) -> None:
    if not svc.can_check(s):
        raise HTTPException(status_code=400, detail="휴강이거나 아직 수업 날이 아니에요.")


def _require_student(db: Session, s: ClassSession, student_id: int) -> Student:
    if student_id not in svc.roster_ids(db, s):
        raise HTTPException(status_code=404, detail="이 수업의 수강생이 아니에요.")
    return db.get(Student, student_id)


# --- 하루 회차 목록 -------------------------------------------------------------


@router.get("")
def day_view(request: Request, user: CurrentUser, db: DBSession, day: str | None = None):
    d = _parse_day(day)
    pairs = svc.sessions_on(db, user, d)
    return render(
        request,
        "attendance/day.html",
        {
            "user": user,
            "day": d,
            "today": date.today(),
            "prev_day": d - timedelta(days=1),
            "next_day": d + timedelta(days=1),
            "weekdays": WEEKDAYS,
            "pairs": pairs,
            "counts": svc.day_counts(db, [s for s, _ in pairs]),
            "teachers": teacher_names(db),
            "ongoing": {s.id for s, _ in pairs if svc.is_ongoing(s)},
        },
    )


# --- 회차 출결 체크 -------------------------------------------------------------


def _board_ctx(db: Session, user: User, s: ClassSession, cls: ClassGroup) -> dict:
    rows = svc.roster(db, s)
    return {
        "user": user,
        "s": s,
        "cls": cls,
        "rows": rows,
        "counts": svc.count(r.status for r in rows),
        "checkable": svc.can_check(s),
        "labels": svc.LABELS,
        "next_status": svc.next_status,
    }


@router.get("/sessions/{session_id}")
def session_view(request: Request, user: CurrentUser, db: DBSession, session_id: int):
    s, cls = _get_session(db, user, session_id)
    ctx = _board_ctx(db, user, s, cls) | {
        "weekdays": WEEKDAYS,
        "teacher": teacher_names(db).get(s.teacher_id),
        "today": date.today(),
    }
    return render(request, "attendance/session.html", ctx, partial="attendance/_board.html")


@router.post("/sessions/{session_id}/students/{student_id}")
def mark(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    session_id: int,
    student_id: int,
    form: FormData,
):
    s, cls = _get_session(db, user, session_id)
    _require_checkable(s)
    student = _require_student(db, s, student_id)
    a = svc.set_status(db, s, student.id, _parse_status(form.get("status")))
    db.commit()
    ctx = _board_ctx(db, user, s, cls)
    return render(
        request,
        "attendance/_tile.html",
        ctx | {"row": svc.RosterRow(student, a), "oob_counts": True},
    )


@router.post("/sessions/{session_id}/all-present")
def all_present(request: Request, user: CurrentUser, db: DBSession, session_id: int):
    """아직 체크하지 않은 학생만 출석으로. 이미 지각·결석으로 체크한 학생은 그대로 둔다."""
    s, cls = _get_session(db, user, session_id)
    _require_checkable(s)
    n = 0
    for row in svc.roster(db, s):
        if row.status is None:
            svc.set_status(db, s, row.student.id, AttendanceStatus.present)
            n += 1
    db.commit()
    return render(
        request,
        "attendance/_board.html",
        _board_ctx(db, user, s, cls),
        headers=trigger_headers(
            toast=f"{n}명을 출석으로 체크했어요" if n else "모두 체크돼 있어요"
        ),
    )


@router.get("/sessions/{session_id}/students/{student_id}/edit")
def edit_mark(request: Request, user: CurrentUser, db: DBSession, session_id: int, student_id: int):
    s, cls = _get_session(db, user, session_id)
    _require_checkable(s)
    student = _require_student(db, s, student_id)
    row = next(r for r in svc.roster(db, s) if r.student.id == student.id)
    return render(
        request,
        "attendance/_edit.html",
        {"s": s, "cls": cls, "row": row, "labels": svc.LABELS},
    )


@router.put("/sessions/{session_id}/students/{student_id}")
def save_mark(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    session_id: int,
    student_id: int,
    form: FormData,
):
    s, _ = _get_session(db, user, session_id)
    _require_checkable(s)
    student = _require_student(db, s, student_id)
    status = _parse_status(form.get("status"))
    memo = (form.get("memo") or "").strip() or None
    svc.set_status(db, s, student.id, status, memo=memo)
    db.commit()
    return hx_trigger("attendanceSaved", toast=f"{student.name} · {svc.LABELS[status]}")
