"""학생 등록과 식별값(조회 토큰, 입금자명) 부여."""

import secrets
from datetime import date

from sqlmodel import Session, col, or_, select

from app.models import Enrollment, Guardian, Student, StudentStatus
from app.schemas import GuardianIn, StudentIn


def new_view_token() -> str:
    return secrets.token_urlsafe(24)


def payer_code_taken(db: Session, code: str, exclude_id: int | None = None) -> bool:
    stmt = select(Student.id).where(Student.payer_code == code)
    if exclude_id is not None:
        stmt = stmt.where(Student.id != exclude_id)
    return db.exec(stmt).first() is not None


def make_payer_code(db: Session, student: Student) -> str:
    """학생명 + 학생번호 끝자리 (예: 홍길동3). 겹치면 끝자리를 한 자리씩 늘린다."""
    sid = str(student.id)
    for n in range(1, len(sid) + 1):
        code = f"{student.name}{sid[-n:]}"
        if not payer_code_taken(db, code, student.id):
            return code
    return f"{student.name}-{sid}"


def create_student(db: Session, data: StudentIn, guardians: list[GuardianIn] = ()) -> Student:
    """id가 정해진 뒤에 payer_code를 부여해야 하므로 flush 후 갱신한다. 커밋은 호출자가 한다."""
    student = Student(
        **data.model_dump(exclude={"payer_code"}),
        view_token=new_view_token(),
        payer_code=f"__pending__{secrets.token_hex(8)}",
    )
    if student.status == StudentStatus.withdrawn:
        student.withdrawn_at = date.today()
    db.add(student)
    db.flush()
    student.payer_code = data.payer_code or make_payer_code(db, student)
    for g in guardians:
        db.add(Guardian(student_id=student.id, **g.model_dump()))
    return student


def set_status(student: Student, status: StudentStatus) -> None:
    if status == StudentStatus.withdrawn and student.status != StudentStatus.withdrawn:
        student.withdrawn_at = date.today()
    elif status != StudentStatus.withdrawn:
        student.withdrawn_at = None
    student.status = status


def end_enrollments(db: Session, student: Student, day: date | None = None) -> int:
    """퇴원 시 진행 중인 수강을 그날까지로 끝낸다. 출석부에서 다음 날부터 빠진다."""
    day = day or date.today()
    rows = db.exec(
        select(Enrollment).where(
            Enrollment.student_id == student.id,
            or_(col(Enrollment.end_date).is_(None), col(Enrollment.end_date) > day),
        )
    ).all()
    for e in rows:
        e.end_date = max(day, e.start_date)
        db.add(e)
    return len(rows)
