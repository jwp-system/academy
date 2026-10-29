from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlmodel import Session, col, select

from academy.auth import AdminUser, CurrentUser
from academy.db import DBSession
from academy.models import (
    Attendance,
    ClassGroup,
    ClassSession,
    Consultation,
    Enrollment,
    Guardian,
    Invoice,
    Notification,
    Role,
    Student,
    StudentStatus,
    User,
)
from academy.schemas import ConsultationIn, GuardianIn, StudentIn, form_errors
from academy.services import excel
from academy.services.students import (
    create_student,
    new_view_token,
    payer_code_taken,
    set_status,
)
from academy.templating import FormData, hx_trigger, render, trigger_headers

router = APIRouter(prefix="/students")


def _visible(stmt, user: User):
    """강사는 담당 반 학생만 (10절: 쿼리 단계에서 teacher_id로 필터링)."""
    if user.role == Role.admin:
        return stmt
    return stmt.where(
        col(Student.id).in_(
            select(Enrollment.student_id)
            .join(ClassGroup, ClassGroup.id == Enrollment.class_id)
            .where(ClassGroup.teacher_id == user.id)
        )
    )


def _get_student(db: Session, user: User, student_id: int) -> Student:
    student = db.exec(_visible(select(Student).where(Student.id == student_id), user)).first()
    if student is None:
        raise HTTPException(status_code=404)
    return student


# --- 목록 -----------------------------------------------------------------


@router.get("")
def list_students(
    request: Request, user: CurrentUser, db: DBSession, q: str = "", status: str = ""
):
    stmt = _visible(select(Student).order_by(Student.name), user)
    if status == "":  # 기본: 재원 + 휴원
        stmt = stmt.where(Student.status != StudentStatus.withdrawn)
    elif status in StudentStatus.__members__:
        stmt = stmt.where(Student.status == StudentStatus(status))
    if q := q.strip():
        stmt = stmt.where(
            col(Student.name).contains(q)
            | col(Student.school).contains(q)
            | col(Student.payer_code).contains(q)
        )
    return render(
        request,
        "students/list.html",
        {"user": user, "students": db.exec(stmt).all(), "q": q, "status": status},
        partial="students/_rows.html",
    )


# --- 등록·수정·퇴원 (원장) --------------------------------------------------


@router.get("/new")
def new_student(request: Request, user: AdminUser):
    return render(request, "students/_form.html", {"student": None, "form": {}, "errors": {}})


@router.post("")
def create(request: Request, user: AdminUser, db: DBSession, form: FormData):
    try:
        data = StudentIn.model_validate(form)
    except ValidationError as e:
        return render(
            request,
            "students/_form.html",
            {"student": None, "form": form, "errors": form_errors(e)},
        )
    student = create_student(db, data)
    db.commit()
    return hx_trigger("studentSaved", toast=f"{student.name} 학생을 등록했어요")


@router.get("/{student_id}/edit")
def edit_student(request: Request, user: AdminUser, db: DBSession, student_id: int):
    student = _get_student(db, user, student_id)
    return render(
        request,
        "students/_form.html",
        {"student": student, "form": student.model_dump(), "errors": {}},
    )


@router.put("/{student_id}")
def update(request: Request, user: AdminUser, db: DBSession, student_id: int, form: FormData):
    student = _get_student(db, user, student_id)
    ctx = {"student": student, "form": form}
    try:
        data = StudentIn.model_validate(form)
    except ValidationError as e:
        return render(request, "students/_form.html", ctx | {"errors": form_errors(e)})
    if not data.payer_code:
        return render(
            request, "students/_form.html", ctx | {"errors": {"payer_code": "꼭 입력해 주세요."}}
        )
    if payer_code_taken(db, data.payer_code, exclude_id=student.id):
        return render(
            request,
            "students/_form.html",
            ctx | {"errors": {"payer_code": "다른 학생이 쓰고 있는 입금자명이에요."}},
        )
    set_status(student, data.status)
    student.sqlmodel_update(data.model_dump(exclude={"status"}))
    db.add(student)
    db.commit()
    return hx_trigger("studentSaved", toast="저장했어요")


@router.delete("/{student_id}")
def withdraw(request: Request, user: AdminUser, db: DBSession, student_id: int):
    """퇴원 처리. 기록 보존을 위해 행은 지우지 않는다 (보관 기간 후 파기는 별도 기능)."""
    student = _get_student(db, user, student_id)
    set_status(student, StudentStatus.withdrawn)
    db.add(student)
    db.commit()
    return render(
        request,
        "students/_row.html",
        {"user": user, "s": student},
        headers=trigger_headers("studentSaved", toast=f"{student.name} 학생을 퇴원 처리했어요"),
    )


@router.post("/{student_id}/token")
def reissue_token(request: Request, user: AdminUser, db: DBSession, student_id: int):
    student = _get_student(db, user, student_id)
    student.view_token = new_view_token()
    db.add(student)
    db.commit()
    return render(
        request,
        "students/_info.html",
        {"user": user, "student": student},
        headers=trigger_headers(toast="새 조회 링크를 만들었어요"),
    )


# --- 엑셀 가져오기 (원장) ----------------------------------------------------


@router.get("/import")
def import_page(request: Request, user: AdminUser):
    return render(request, "students/import.html", {"user": user})


@router.get("/import/template.xlsx")
def import_template(user: AdminUser):
    return Response(
        excel.student_template(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''students_template.xlsx"},
    )


@router.post("/import")
def import_upload(
    request: Request, user: AdminUser, db: DBSession, file: Annotated[UploadFile, File()]
):
    result = excel.import_students(db, file.file)
    return render(request, "students/_import_result.html", {"result": result})


# --- 상세 -----------------------------------------------------------------


@router.get("/{student_id}")
def detail(request: Request, user: CurrentUser, db: DBSession, student_id: int):
    student = _get_student(db, user, student_id)
    enrollments = db.exec(
        select(Enrollment, ClassGroup)
        .join(ClassGroup, ClassGroup.id == Enrollment.class_id)
        .where(Enrollment.student_id == student.id)
        .order_by(col(Enrollment.start_date).desc())
    ).all()
    attendances = db.exec(
        select(Attendance, ClassSession, ClassGroup)
        .join(ClassSession, ClassSession.id == Attendance.session_id)
        .join(ClassGroup, ClassGroup.id == ClassSession.class_id)
        .where(Attendance.student_id == student.id)
        .order_by(col(ClassSession.date).desc())
        .limit(50)
    ).all()
    ctx = {
        "user": user,
        "student": student,
        "enrollments": enrollments,
        "attendances": attendances,
        **_guardians_ctx(db, student),
        **_consultations_ctx(db, student),
    }
    if user.role == Role.admin:
        ctx["invoices"] = db.exec(
            select(Invoice)
            .where(Invoice.student_id == student.id)
            .order_by(col(Invoice.year_month).desc())
        ).all()
        ctx["notifications"] = db.exec(
            select(Notification)
            .where(Notification.student_id == student.id)
            .order_by(col(Notification.scheduled_at).desc())
            .limit(50)
        ).all()
    return render(request, "students/detail.html", ctx)


@router.get("/{student_id}/info")
def info(request: Request, user: CurrentUser, db: DBSession, student_id: int):
    student = _get_student(db, user, student_id)
    return render(request, "students/_info.html", {"user": user, "student": student})


# --- 보호자 (원장) ------------------------------------------------------------


def _guardians_ctx(db: Session, student: Student, **extra) -> dict:
    guardians = db.exec(
        select(Guardian).where(Guardian.student_id == student.id).order_by(Guardian.id)
    ).all()
    return {"student": student, "guardians": guardians, "form": {}, "errors": {}, **extra}


def _get_guardian(db: Session, student: Student, guardian_id: int) -> Guardian:
    guardian = db.get(Guardian, guardian_id)
    if guardian is None or guardian.student_id != student.id:
        raise HTTPException(status_code=404)
    return guardian


@router.post("/{student_id}/guardians")
def add_guardian(request: Request, user: AdminUser, db: DBSession, student_id: int, form: FormData):
    student = _get_student(db, user, student_id)
    try:
        data = GuardianIn.model_validate(form)
    except ValidationError as e:
        ctx = _guardians_ctx(db, student, user=user, form=form, errors=form_errors(e))
        return render(request, "students/_guardians.html", ctx)
    db.add(Guardian(student_id=student.id, **data.model_dump()))
    db.commit()
    return render(
        request,
        "students/_guardians.html",
        _guardians_ctx(db, student, user=user),
        headers=trigger_headers(toast="보호자를 추가했어요"),
    )


@router.get("/{student_id}/guardians/{guardian_id}/edit")
def edit_guardian(
    request: Request, user: AdminUser, db: DBSession, student_id: int, guardian_id: int
):
    student = _get_student(db, user, student_id)
    guardian = _get_guardian(db, student, guardian_id)
    return render(
        request,
        "students/_guardian_edit.html",
        {"student": student, "g": guardian, "form": guardian.model_dump(), "errors": {}},
    )


@router.put("/{student_id}/guardians/{guardian_id}")
def update_guardian(
    request: Request,
    user: AdminUser,
    db: DBSession,
    student_id: int,
    guardian_id: int,
    form: FormData,
):
    student = _get_student(db, user, student_id)
    guardian = _get_guardian(db, student, guardian_id)
    try:
        data = GuardianIn.model_validate(form)
    except ValidationError as e:
        return render(
            request,
            "students/_guardian_edit.html",
            {"student": student, "g": guardian, "form": form, "errors": form_errors(e)},
        )
    guardian.sqlmodel_update(data.model_dump())
    db.add(guardian)
    db.commit()
    # 수정 폼은 자기 자신을 target으로 두므로(검증 오류 시 폼만 교체), 성공 시 목록 전체로 바꾼다
    return render(
        request,
        "students/_guardians.html",
        _guardians_ctx(db, student, user=user),
        headers={"HX-Retarget": "#guardians", "HX-Reswap": "outerHTML"}
        | trigger_headers(toast="저장했어요"),
    )


@router.delete("/{student_id}/guardians/{guardian_id}")
def delete_guardian(
    request: Request, user: AdminUser, db: DBSession, student_id: int, guardian_id: int
):
    student = _get_student(db, user, student_id)
    db.delete(_get_guardian(db, student, guardian_id))
    db.commit()
    return render(
        request,
        "students/_guardians.html",
        _guardians_ctx(db, student, user=user),
        headers=trigger_headers(toast="보호자를 삭제했어요"),
    )


# --- 상담 메모 (원장, 담당 강사) -------------------------------------------------


def _consultations_ctx(db: Session, student: Student, **extra) -> dict:
    rows = db.exec(
        select(Consultation, User)
        .join(User, User.id == Consultation.user_id, isouter=True)
        .where(Consultation.student_id == student.id)
        .order_by(col(Consultation.date).desc(), col(Consultation.id).desc())
    ).all()
    return {"student": student, "consultations": rows, "c_form": {}, "c_errors": {}, **extra}


@router.post("/{student_id}/consultations")
def add_consultation(
    request: Request, user: CurrentUser, db: DBSession, student_id: int, form: FormData
):
    student = _get_student(db, user, student_id)
    try:
        data = ConsultationIn.model_validate(form)
    except ValidationError as e:
        ctx = _consultations_ctx(db, student, user=user, c_form=form, c_errors=form_errors(e))
        return render(request, "students/_consultations.html", ctx)
    db.add(Consultation(student_id=student.id, user_id=user.id, content=data.content))
    db.commit()
    return render(
        request, "students/_consultations.html", _consultations_ctx(db, student, user=user)
    )


@router.delete("/{student_id}/consultations/{consultation_id}")
def delete_consultation(
    request: Request, user: CurrentUser, db: DBSession, student_id: int, consultation_id: int
):
    student = _get_student(db, user, student_id)
    c = db.get(Consultation, consultation_id)
    if c is None or c.student_id != student.id:
        raise HTTPException(status_code=404)
    if user.role != Role.admin and c.user_id != user.id:
        raise HTTPException(status_code=403, detail="본인이 쓴 기록만 삭제할 수 있어요.")
    db.delete(c)
    db.commit()
    return render(
        request, "students/_consultations.html", _consultations_ctx(db, student, user=user)
    )
