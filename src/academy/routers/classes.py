"""반·정규 시간표·수강생·회차, 주간 시간표(FullCalendar).

원장은 모두 관리하고, 강사는 담당 반(과 대강 회차)을 조회만 한다.
시간이 겹치면 막지 않고 경고를 보여준 뒤 '겹쳐도 저장'으로 진행할 수 있다.
"""

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlmodel import Session, col, func, or_, select

from academy.auth import AdminUser, CurrentUser
from academy.db import DBSession
from academy.models import (
    ClassGroup,
    ClassSchedule,
    ClassSession,
    Enrollment,
    Role,
    SessionStatus,
    Student,
    StudentStatus,
    User,
)
from academy.schemas import ClassIn, EnrollIn, ScheduleIn, SessionIn, form_errors
from academy.services import sessions as svc
from academy.templating import FormData, hx_trigger, render, trigger_headers

router = APIRouter(prefix="/classes")


def _get_class(db: Session, user: User, class_id: int) -> ClassGroup:
    cls = db.get(ClassGroup, class_id)
    if cls is None or (user.role != Role.admin and cls.teacher_id != user.id):
        raise HTTPException(status_code=404)
    return cls


def _get_session(db: Session, session_id: int) -> tuple[ClassSession, ClassGroup]:
    s = db.get(ClassSession, session_id)
    if s is None:
        raise HTTPException(status_code=404)
    return s, db.get(ClassGroup, s.class_id)


def _teacher_options(db: Session) -> list[tuple[str, str]]:
    users = db.exec(select(User).where(User.active).order_by(User.role.desc(), User.name)).all()
    return [("", "미정")] + [(str(u.id), u.name) for u in users]


def _rooms(db: Session) -> list[str]:
    return sorted({r for r in db.exec(select(ClassGroup.room)).all() if r})


def _check_teacher(db: Session, teacher_id: int | None) -> str | None:
    if teacher_id is None:
        return None
    t = db.get(User, teacher_id)
    return None if t and t.active else "사용 중인 계정을 골라 주세요."


def _is_forced(form: dict) -> bool:
    return form.get("force") == "1"


def _schedule_summary(schedules: list[ClassSchedule]) -> list[str]:
    return [
        f"{svc.WEEKDAYS[s.weekday]} {svc.fmt_time(s.start_time)}~{svc.fmt_time(s.end_time)}"
        for s in sorted(schedules, key=lambda s: (s.weekday, s.start_time))
    ]


def _active_enrollments(today: date):
    return or_(col(Enrollment.end_date).is_(None), col(Enrollment.end_date) >= today)


# --- 목록 -----------------------------------------------------------------


def _list_ctx(db: Session, user: User, show_inactive: bool) -> dict[str, Any]:
    stmt = select(ClassGroup).order_by(col(ClassGroup.active).desc(), ClassGroup.name)
    if user.role != Role.admin:
        stmt = stmt.where(ClassGroup.teacher_id == user.id)
    if not show_inactive:
        stmt = stmt.where(ClassGroup.active)
    classes = db.exec(stmt).all()
    ids = [c.id for c in classes]
    schedules: dict[int, list[ClassSchedule]] = {}
    for s in db.exec(select(ClassSchedule).where(col(ClassSchedule.class_id).in_(ids))).all():
        schedules.setdefault(s.class_id, []).append(s)
    counts = dict(
        db.exec(
            select(Enrollment.class_id, func.count())
            .where(col(Enrollment.class_id).in_(ids), _active_enrollments(date.today()))
            .group_by(Enrollment.class_id)
        ).all()
    )
    return {
        "user": user,
        "classes": classes,
        "schedules": {cid: _schedule_summary(v) for cid, v in schedules.items()},
        "counts": counts,
        "teachers": svc.teacher_names(db),
        "show_inactive": show_inactive,
    }


@router.get("")
def list_classes(request: Request, user: CurrentUser, db: DBSession, show_inactive: bool = False):
    return render(
        request,
        "classes/list.html",
        _list_ctx(db, user, show_inactive),
        partial="classes/_rows.html",
    )


@router.post("/generate")
def generate(user: AdminUser, db: DBSession):
    n = svc.generate_sessions(db, date.today())
    db.commit()
    msg = (
        f"앞으로 {svc.DEFAULT_WEEKS}주 회차 {n}개를 만들었어요" if n else "새로 만들 회차가 없어요"
    )
    return hx_trigger("sessionSaved", toast=msg)


# --- 주간 시간표 -------------------------------------------------------------


@router.get("/calendar")
def calendar(request: Request, user: CurrentUser, db: DBSession):
    return render(
        request,
        "classes/calendar.html",
        {"user": user, "teacher_options": _teacher_options(db)[1:], "rooms": _rooms(db)},
    )


@router.get("/events")
def events(
    user: CurrentUser,
    db: DBSession,
    start: str,
    end: str,
    teacher_id: str = "",
    room: str = "",
):
    """FullCalendar 이벤트 피드. start/end는 ISO 문자열(시간대 포함)이라 날짜 부분만 쓴다."""
    try:
        start_d, end_d = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError:
        raise HTTPException(status_code=400) from None
    stmt = (
        select(ClassSession, ClassGroup)
        .join(ClassGroup, ClassGroup.id == ClassSession.class_id)
        .where(ClassSession.date >= start_d, ClassSession.date < end_d)
    )
    if user.role != Role.admin:
        stmt = stmt.where(or_(ClassSession.teacher_id == user.id, ClassGroup.teacher_id == user.id))
    elif teacher_id.isdigit():
        stmt = stmt.where(ClassSession.teacher_id == int(teacher_id))
    if room:
        stmt = stmt.where(ClassSession.room == room)
    names = svc.teacher_names(db)
    out = []
    for s, cls in db.exec(stmt).all():
        color = svc.class_color(cls.id)
        title = cls.name
        if s.status == SessionStatus.makeup:
            title = f"[보강] {title}"
        out.append(
            {
                "id": s.id,
                "title": title,
                "start": f"{s.date}T{svc.fmt_time(s.start_time)}",
                "end": f"{s.date}T{svc.fmt_time(s.end_time)}",
                "backgroundColor": color + "1f",
                "borderColor": color,
                "textColor": color,
                "classNames": [f"is-{s.status}"],
                "extendedProps": {
                    "classId": cls.id,
                    "room": s.room or "",
                    "teacher": names.get(s.teacher_id, ""),
                    "status": s.status,
                },
            }
        )
    return JSONResponse(out)


# --- 반 등록·수정 (원장) -------------------------------------------------------


def _class_form(request, db, cls, form, errors, conflicts=()):
    return render(
        request,
        "classes/_form.html",
        {
            "cls": cls,
            "form": form,
            "errors": errors,
            "conflicts": conflicts,
            "teacher_options": _teacher_options(db),
            "rooms": _rooms(db),
        },
    )


@router.get("/new")
def new_class(request: Request, user: AdminUser, db: DBSession):
    return _class_form(request, db, None, {"active": True}, {})


@router.post("")
def create_class(request: Request, user: AdminUser, db: DBSession, form: FormData):
    try:
        data = ClassIn.model_validate(form)
    except ValidationError as e:
        return _class_form(request, db, None, form, form_errors(e))
    if err := _check_teacher(db, data.teacher_id):
        return _class_form(request, db, None, form, {"teacher_id": err})
    # 새 반은 항상 운영 중으로 시작한다 (운영 여부 체크박스는 수정 화면에만 있다)
    cls = ClassGroup(**data.model_dump(exclude={"active"}), active=True)
    db.add(cls)
    db.commit()
    return hx_trigger(
        "classSaved", toast=f"{cls.name} 반을 만들었어요. 상세 화면에서 시간표를 추가해 주세요"
    )


@router.get("/sessions/{session_id}/edit")
def edit_session(request: Request, user: AdminUser, db: DBSession, session_id: int):
    s, cls = _get_session(db, session_id)
    return _session_form(request, db, cls, s, s.model_dump(), {})


@router.put("/sessions/{session_id}")
def update_session(
    request: Request, user: AdminUser, db: DBSession, session_id: int, form: FormData
):
    s, cls = _get_session(db, session_id)
    try:
        data = SessionIn.model_validate(form)
    except ValidationError as e:
        return _session_form(request, db, cls, s, form, form_errors(e))
    if err := _check_teacher(db, data.teacher_id):
        return _session_form(request, db, cls, s, form, {"teacher_id": err})
    conflicts = svc.session_conflicts(
        db,
        day=data.date,
        start=data.start_time,
        end=data.end_time,
        room=data.room,
        teacher_id=data.teacher_id,
        exclude_session_id=s.id,
    )
    if conflicts and not _is_forced(form):
        return _session_form(request, db, cls, s, form, {}, conflicts)
    s.sqlmodel_update(data.model_dump())
    db.add(s)
    db.commit()
    return hx_trigger("sessionSaved", toast="회차를 바꿨어요")


def _set_session_status(db: Session, session_id: int, status: SessionStatus) -> ClassSession:
    s, _ = _get_session(db, session_id)
    s.status = status
    db.add(s)
    db.commit()
    return s


@router.post("/sessions/{session_id}/cancel")
def cancel_session(user: AdminUser, db: DBSession, session_id: int):
    _set_session_status(db, session_id, SessionStatus.cancelled)
    return hx_trigger("sessionSaved", toast="휴강으로 바꿨어요")


@router.post("/sessions/{session_id}/restore")
def restore_session(user: AdminUser, db: DBSession, session_id: int):
    s, _ = _get_session(db, session_id)
    # 정규 회차는 정상으로, 수동으로 만든 회차는 보강으로 되돌린다
    status = SessionStatus.normal if s.schedule_id else SessionStatus.makeup
    _set_session_status(db, session_id, status)
    return hx_trigger("sessionSaved", toast="휴강을 취소했어요")


@router.get("/{class_id}/edit")
def edit_class(request: Request, user: AdminUser, db: DBSession, class_id: int):
    cls = _get_class(db, user, class_id)
    return _class_form(request, db, cls, cls.model_dump(), {})


@router.put("/{class_id}")
def update_class(request: Request, user: AdminUser, db: DBSession, class_id: int, form: FormData):
    cls = _get_class(db, user, class_id)
    try:
        data = ClassIn.model_validate(form)
    except ValidationError as e:
        return _class_form(request, db, cls, form, form_errors(e))
    if err := _check_teacher(db, data.teacher_id):
        return _class_form(request, db, cls, form, {"teacher_id": err})

    old_room, old_teacher, was_active = cls.room, cls.teacher_id, cls.active
    cls.sqlmodel_update(data.model_dump())
    if (cls.room, cls.teacher_id) != (old_room, old_teacher) and cls.active:
        conflicts = svc.class_schedule_conflicts(db, cls)
        if conflicts and not _is_forced(form):
            db.rollback()
            db.refresh(cls)
            return _class_form(request, db, cls, form, {}, conflicts)

    today = date.today()
    notes = []
    if was_active and not cls.active:
        if n := svc.remove_future_sessions(db, today, class_id=cls.id):
            notes.append(f"앞으로의 회차 {n}개를 지웠어요")
    elif cls.active:
        if not was_active and (n := svc.generate_sessions(db, today, class_id=cls.id)):
            notes.append(f"회차 {n}개를 만들었어요")
        if n := svc.sync_future_sessions(
            db, cls, today, old_room=old_room, old_teacher_id=old_teacher
        ):
            notes.append(f"앞으로의 회차 {n}개에 반영했어요")
    db.add(cls)
    db.commit()
    return hx_trigger("classSaved", "sessionSaved", toast=" · ".join(["저장했어요", *notes]))


# --- 상세 -----------------------------------------------------------------


@router.get("/{class_id}")
def detail(request: Request, user: CurrentUser, db: DBSession, class_id: int):
    cls = _get_class(db, user, class_id)
    ctx = {
        "user": user,
        "cls": cls,
        "teacher": db.get(User, cls.teacher_id) if cls.teacher_id else None,
        **_students_ctx(db, cls),
        **_schedules_ctx(db, cls),
        **_sessions_ctx(db, cls),
    }
    return render(request, "classes/detail.html", ctx)


# --- 정규 시간표 ---------------------------------------------------------------


def _schedules_ctx(db: Session, cls: ClassGroup, **extra) -> dict[str, Any]:
    schedules = db.exec(
        select(ClassSchedule)
        .where(ClassSchedule.class_id == cls.id)
        .order_by(ClassSchedule.weekday, ClassSchedule.start_time)
    ).all()
    return {
        "cls": cls,
        "schedules": schedules,
        "s_form": {},
        "s_errors": {},
        "s_conflicts": [],
        "weekdays": svc.WEEKDAYS,
        **extra,
    }


@router.post("/{class_id}/schedules")
def add_schedule(request: Request, user: AdminUser, db: DBSession, class_id: int, form: FormData):
    cls = _get_class(db, user, class_id)

    def again(**kw):
        return render(
            request,
            "classes/_schedules.html",
            {"user": user, **_schedules_ctx(db, cls, s_form=form, **kw)},
        )

    try:
        data = ScheduleIn.model_validate(form)
    except ValidationError as e:
        return again(s_errors=form_errors(e))
    # 같은 반 안에서 겹치는 시간표는 경고가 아니라 오류
    for other in db.exec(
        select(ClassSchedule).where(
            ClassSchedule.class_id == cls.id, ClassSchedule.weekday == data.weekday
        )
    ).all():
        if svc.overlaps(data.start_time, data.end_time, other.start_time, other.end_time):
            return again(s_errors={"start_time": "이 반에 이미 겹치는 시간표가 있어요."})
    conflicts = svc.schedule_conflicts(
        db,
        weekday=data.weekday,
        start=data.start_time,
        end=data.end_time,
        room=cls.room,
        teacher_id=cls.teacher_id,
        exclude_class_id=cls.id,
    )
    if conflicts and not _is_forced(form):
        return again(s_conflicts=conflicts)
    db.add(ClassSchedule(class_id=cls.id, **data.model_dump()))
    db.flush()
    n = svc.generate_sessions(db, date.today(), class_id=cls.id) if cls.active else 0
    db.commit()
    return render(
        request,
        "classes/_schedules.html",
        {"user": user, **_schedules_ctx(db, cls)},
        headers=trigger_headers("sessionSaved", toast=f"시간표를 추가하고 회차 {n}개를 만들었어요"),
    )


@router.delete("/{class_id}/schedules/{schedule_id}")
def delete_schedule(
    request: Request, user: AdminUser, db: DBSession, class_id: int, schedule_id: int
):
    cls = _get_class(db, user, class_id)
    sched = db.get(ClassSchedule, schedule_id)
    if sched is None or sched.class_id != cls.id:
        raise HTTPException(status_code=404)
    n = svc.remove_future_sessions(db, date.today(), schedule_id=sched.id)
    db.delete(sched)
    db.commit()
    toast = "시간표를 지웠어요" + (f" · 앞으로의 회차 {n}개도 지웠어요" if n else "")
    return render(
        request,
        "classes/_schedules.html",
        {"user": user, **_schedules_ctx(db, cls)},
        headers=trigger_headers("sessionSaved", toast=toast),
    )


# --- 수강생 -----------------------------------------------------------------


def _students_ctx(db: Session, cls: ClassGroup) -> dict[str, Any]:
    today = date.today()
    rows = db.exec(
        select(Enrollment, Student)
        .join(Student, Student.id == Enrollment.student_id)
        .where(Enrollment.class_id == cls.id)
        .order_by(Student.name)
    ).all()
    current = [(e, s) for e, s in rows if e.end_date is None or e.end_date >= today]
    past = sorted(
        [(e, s) for e, s in rows if e.end_date is not None and e.end_date < today],
        key=lambda r: r[0].end_date,
        reverse=True,
    )
    return {"cls": cls, "current": current, "past": past}


def _students_response(request, db, user, cls, toast=None):
    return render(
        request,
        "classes/_students.html",
        {"user": user, **_students_ctx(db, cls)},
        headers=trigger_headers("enrollmentChanged", toast=toast),
    )


@router.get("/{class_id}/enroll-search")
def enroll_search(request: Request, user: AdminUser, db: DBSession, class_id: int, q: str = ""):
    cls = _get_class(db, user, class_id)
    enrolled = select(Enrollment.student_id).where(
        Enrollment.class_id == cls.id, _active_enrollments(date.today())
    )
    stmt = (
        select(Student)
        .where(Student.status != StudentStatus.withdrawn, col(Student.id).not_in(enrolled))
        .order_by(Student.name)
        .limit(20)
    )
    if q := q.strip():
        stmt = stmt.where(col(Student.name).contains(q) | col(Student.school).contains(q))
    return render(
        request, "classes/_enroll_search.html", {"cls": cls, "students": db.exec(stmt).all()}
    )


@router.post("/{class_id}/enrollments")
def enroll(request: Request, user: AdminUser, db: DBSession, class_id: int, form: FormData):
    cls = _get_class(db, user, class_id)
    try:
        data = EnrollIn.model_validate(form)
    except ValidationError:
        raise HTTPException(status_code=400, detail="학생을 골라 주세요.") from None
    student = db.get(Student, data.student_id)
    if student is None:
        raise HTTPException(status_code=404)
    already = db.exec(
        select(Enrollment.id).where(
            Enrollment.class_id == cls.id,
            Enrollment.student_id == student.id,
            _active_enrollments(date.today()),
        )
    ).first()
    if already:
        raise HTTPException(status_code=409, detail=f"{student.name} 학생은 이미 수강 중이에요.")
    db.add(Enrollment(class_id=cls.id, **data.model_dump()))
    db.commit()
    return _students_response(request, db, user, cls, f"{student.name} 학생을 추가했어요")


@router.post("/{class_id}/enrollments/{enrollment_id}/end")
def end_enrollment(
    request: Request, user: AdminUser, db: DBSession, class_id: int, enrollment_id: int
):
    cls = _get_class(db, user, class_id)
    e = db.get(Enrollment, enrollment_id)
    if e is None or e.class_id != cls.id:
        raise HTTPException(status_code=404)
    # 오늘 수업까지는 듣고 내일부터 빠진다
    e.end_date = max(date.today(), e.start_date)
    db.add(e)
    db.commit()
    student = db.get(Student, e.student_id)
    return _students_response(request, db, user, cls, f"{student.name} 학생의 수강을 종료했어요")


# --- 회차 -----------------------------------------------------------------


def _sessions_ctx(db: Session, cls: ClassGroup) -> dict[str, Any]:
    today = date.today()
    sessions = db.exec(
        select(ClassSession)
        .where(
            ClassSession.class_id == cls.id,
            ClassSession.date >= today - timedelta(days=7),
            ClassSession.date < today + timedelta(weeks=svc.DEFAULT_WEEKS),
        )
        .order_by(ClassSession.date, ClassSession.start_time)
    ).all()
    return {
        "cls": cls,
        "sessions": sessions,
        "today": today,
        "teachers": svc.teacher_names(db),
        "weekdays": svc.WEEKDAYS,
    }


@router.get("/{class_id}/sessions")
def sessions_list(request: Request, user: CurrentUser, db: DBSession, class_id: int):
    cls = _get_class(db, user, class_id)
    return render(request, "classes/_sessions.html", {"user": user, **_sessions_ctx(db, cls)})


def _session_form(request, db, cls, session, form, errors, conflicts=()):
    return render(
        request,
        "classes/_session_form.html",
        {
            "cls": cls,
            "session": session,
            "form": form,
            "errors": errors,
            "conflicts": conflicts,
            "teacher_options": _teacher_options(db),
            "rooms": _rooms(db),
        },
    )


@router.get("/{class_id}/sessions/new")
def new_makeup(request: Request, user: AdminUser, db: DBSession, class_id: int):
    cls = _get_class(db, user, class_id)
    form = {"room": cls.room, "teacher_id": cls.teacher_id, "date": date.today()}
    return _session_form(request, db, cls, None, form, {})


@router.post("/{class_id}/sessions")
def create_makeup(request: Request, user: AdminUser, db: DBSession, class_id: int, form: FormData):
    cls = _get_class(db, user, class_id)
    try:
        data = SessionIn.model_validate(form)
    except ValidationError as e:
        return _session_form(request, db, cls, None, form, form_errors(e))
    if err := _check_teacher(db, data.teacher_id):
        return _session_form(request, db, cls, None, form, {"teacher_id": err})
    conflicts = svc.session_conflicts(
        db,
        day=data.date,
        start=data.start_time,
        end=data.end_time,
        room=data.room,
        teacher_id=data.teacher_id,
    )
    if conflicts and not _is_forced(form):
        return _session_form(request, db, cls, None, form, {}, conflicts)
    db.add(ClassSession(class_id=cls.id, status=SessionStatus.makeup, **data.model_dump()))
    db.commit()
    return hx_trigger(
        "sessionSaved", toast=f"{data.date.month}월 {data.date.day}일 보강 회차를 만들었어요"
    )
