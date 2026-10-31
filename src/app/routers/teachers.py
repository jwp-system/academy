"""강사(사용자 계정) 관리. 원장 전용. 계정은 삭제하지 않고 비활성화한다."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.auth import AdminUser, hash_password
from app.db import DBSession
from app.models import ClassGroup, Role, User
from app.schemas import TeacherIn, form_errors
from app.templating import FormData, hx_trigger, render

router = APIRouter(prefix="/teachers")


def _rows_ctx(db: Session, user: User, show_inactive: bool) -> dict:
    stmt = select(User).order_by(col(User.active).desc(), User.role, User.name)
    if not show_inactive:
        stmt = stmt.where(User.active)
    users = db.exec(stmt).all()
    classes: dict[int, list[str]] = {}
    for c in db.exec(select(ClassGroup).where(ClassGroup.active).order_by(ClassGroup.name)).all():
        if c.teacher_id:
            classes.setdefault(c.teacher_id, []).append(c.name)
    return {"user": user, "users": users, "classes": classes, "show_inactive": show_inactive}


@router.get("")
def list_teachers(request: Request, user: AdminUser, db: DBSession, show_inactive: bool = False):
    return render(
        request,
        "teachers/list.html",
        _rows_ctx(db, user, show_inactive),
        partial="teachers/_rows.html",
    )


def _form(request: Request, target: User | None, form: dict, errors: dict):
    return render(
        request, "teachers/_form.html", {"target": target, "form": form, "errors": errors}
    )


def _email_taken(db: Session, email: str, exclude_id: int | None = None) -> bool:
    stmt = select(User.id).where(User.email == email)
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return db.exec(stmt).first() is not None


@router.get("/new")
def new_teacher(request: Request, user: AdminUser):
    return _form(request, None, {"active": True, "role": Role.teacher}, {})


@router.post("")
def create(request: Request, user: AdminUser, db: DBSession, form: FormData):
    try:
        data = TeacherIn.model_validate(form)
    except ValidationError as e:
        return _form(request, None, form, form_errors(e))
    if not data.password:
        return _form(request, None, form, {"password": "꼭 입력해 주세요."})
    if _email_taken(db, data.email):
        return _form(request, None, form, {"email": "이미 등록된 이메일이에요."})
    db.add(
        User(
            **data.model_dump(exclude={"password"}),
            password_hash=hash_password(data.password),
        )
    )
    db.commit()
    return hx_trigger("teacherSaved", toast=f"{data.name}님 계정을 만들었어요")


def _get_user(db: Session, user_id: int) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    return target


@router.get("/{user_id}/edit")
def edit_teacher(request: Request, user: AdminUser, db: DBSession, user_id: int):
    target = _get_user(db, user_id)
    return _form(request, target, target.model_dump(exclude={"password_hash"}), {})


@router.put("/{user_id}")
def update(request: Request, user: AdminUser, db: DBSession, user_id: int, form: FormData):
    target = _get_user(db, user_id)
    try:
        data = TeacherIn.model_validate(form)
    except ValidationError as e:
        return _form(request, target, form, form_errors(e))
    if _email_taken(db, data.email, exclude_id=target.id):
        return _form(request, target, form, {"email": "이미 등록된 이메일이에요."})
    if target.id == user.id and (data.role != Role.admin or not data.active):
        return _form(request, target, form, {"role": "본인 계정은 권한을 바꾸거나 끌 수 없어요."})
    target.sqlmodel_update(data.model_dump(exclude={"password"}))
    if data.password:
        target.password_hash = hash_password(data.password)
    db.add(target)
    db.commit()
    return hx_trigger("teacherSaved", toast="저장했어요")
