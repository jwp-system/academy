import json
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlmodel import select

from app.models import ClassGroup, Consultation, Enrollment, Guardian, Role, Student, User
from app.schemas import StudentIn
from app.services.students import create_student


@pytest.fixture
def admin(client, login):
    token = login()
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return client


def _student(db, name="홍길동", **kw):
    s = create_student(db, StudentIn(name=name, **kw))
    db.commit()
    return s


def test_create_student_assigns_payer_code_and_token(admin, db):
    resp = admin.post("/students", data={"name": "홍길동", "phone": "010-1234-5678"})
    assert resp.status_code == 200
    assert "studentSaved" in json.loads(resp.headers["HX-Trigger"])
    s = db.exec(select(Student)).one()
    assert s.payer_code == f"홍길동{str(s.id)[-1]}"
    assert s.phone == "01012345678"
    assert len(s.view_token) >= 32


def test_payer_code_collision_extends_digits(db):
    # id 1과 11은 끝자리가 같다
    first = _student(db)
    for i in range(9):
        _student(db, name=f"학생{i}")
    eleventh = _student(db)
    assert eleventh.id == 11
    assert first.payer_code == "홍길동1"
    assert eleventh.payer_code == "홍길동11"


def test_create_student_validation_rerenders_form(admin, db):
    resp = admin.post("/students", data={"name": "", "phone": "123"})
    assert "HX-Trigger" not in resp.headers
    assert "꼭 입력해 주세요." in resp.text
    assert "전화번호 형식을 확인해 주세요." in resp.text
    assert db.exec(select(Student)).first() is None


def test_update_rejects_duplicate_payer_code(admin, db):
    a, b = _student(db, "가"), _student(db, "나")
    resp = admin.put(f"/students/{b.id}", data={"name": "나", "payer_code": a.payer_code})
    assert "다른 학생이 쓰고 있는 입금자명이에요." in resp.text


def test_withdraw_keeps_row_and_hides_from_default_list(admin, db):
    s = _student(db)
    resp = admin.delete(f"/students/{s.id}")
    assert "퇴원" in resp.text
    db.refresh(s)
    assert s.status == "withdrawn" and s.withdrawn_at is not None
    assert "홍길동" not in admin.get("/students").text
    assert "홍길동" in admin.get("/students?status=withdrawn").text


def test_search(admin, db):
    _student(db, "김철수", school="한빛중")
    _student(db, "이영희")
    rows = admin.get("/students?q=한빛").text
    assert "김철수" in rows and "이영희" not in rows


def test_guardian_add_edit_delete(admin, db):
    s = _student(db)
    resp = admin.post(
        f"/students/{s.id}/guardians",
        data={"name": "홍판서", "phone": "01099998888", "notify_sms": "on"},
    )
    assert "010-9999-8888" in resp.text
    g = db.exec(select(Guardian)).one()
    assert g.notify_sms is True

    resp = admin.put(f"/students/{s.id}/guardians/{g.id}", data={"name": "홍판서", "email": "bad"})
    assert "이메일 형식을 확인해 주세요." in resp.text
    resp = admin.put(f"/students/{s.id}/guardians/{g.id}", data={"name": "홍대감"})
    assert resp.headers["HX-Retarget"] == "#guardians"
    db.refresh(g)
    assert g.name == "홍대감" and g.notify_sms is False

    admin.delete(f"/students/{s.id}/guardians/{g.id}")
    assert db.exec(select(Guardian)).first() is None


def test_reissue_token(admin, db):
    s = _student(db)
    old = s.view_token
    admin.post(f"/students/{s.id}/token")
    db.refresh(s)
    assert s.view_token != old


def test_detail_page(admin, db):
    s = _student(db)
    resp = admin.get(f"/students/{s.id}", headers={"HX-Request": ""})
    assert resp.status_code == 200
    assert "학부모 조회 링크" in resp.text


# --- 강사 권한 ----------------------------------------------------------------


@pytest.fixture
def teacher(client, login, db):
    token = login(Role.teacher)
    client.headers.update({"X-CSRF-Token": token, "HX-Request": "true"})
    return db.exec(select(User).where(User.role == Role.teacher)).one()


def _assign(db, teacher_user, student):
    c = ClassGroup(name="중2 수학", teacher_id=teacher_user.id)
    db.add(c)
    db.flush()
    db.add(Enrollment(student_id=student.id, class_id=c.id, start_date=student.enrolled_at))
    db.commit()


def test_teacher_sees_only_own_students(client, teacher, db):
    mine, other = _student(db, "내학생"), _student(db, "남의학생")
    _assign(db, teacher, mine)
    rows = client.get("/students").text
    assert "내학생" in rows and "남의학생" not in rows
    assert client.get(f"/students/{other.id}").status_code == 404
    assert client.get(f"/students/{mine.id}").status_code == 200


def test_teacher_cannot_edit_but_can_add_consultation(client, teacher, db):
    s = _student(db)
    _assign(db, teacher, s)
    assert client.post("/students", data={"name": "x"}).status_code == 403
    assert client.put(f"/students/{s.id}", data={"name": "x"}).status_code == 403
    assert client.delete(f"/students/{s.id}").status_code == 403

    resp = client.post(f"/students/{s.id}/consultations", data={"content": "진도 상담"})
    assert "진도 상담" in resp.text
    c = db.exec(select(Consultation)).one()
    assert c.user_id == teacher.id


def test_teacher_cannot_delete_others_consultation(client, teacher, db):
    s = _student(db)
    _assign(db, teacher, s)
    c = Consultation(student_id=s.id, user_id=None, content="원장 메모")
    db.add(c)
    db.commit()
    assert client.delete(f"/students/{s.id}/consultations/{c.id}").status_code == 403


# --- 엑셀 가져오기 --------------------------------------------------------------


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["이름", "학교", "학년", "학생연락처", "상태", "보호자명", "보호자연락처"])
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client, content):
    return client.post(
        "/students/import",
        files={
            "file": (
                "s.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def test_import_students(admin, db):
    # 엑셀이 전화번호를 숫자로 저장해 앞자리 0이 빠진 경우와 학년이 숫자인 경우
    content = _xlsx(
        [
            ["김하나", "한빛중", 2, 1012345678, "재원", "김엄마", "010-1111-2222"],
            ["박둘", None, None, None, "휴원", None, None],
            [None, None, None, None, None, None, None],
        ]
    )
    resp = _upload(admin, content)
    assert "2명을 등록했어요" in resp.text
    students = {s.name: s for s in db.exec(select(Student)).all()}
    assert students["김하나"].phone == "01012345678"
    assert students["김하나"].grade == "2"
    assert students["박둘"].status == "paused"
    g = db.exec(select(Guardian)).one()
    assert g.name == "김엄마" and g.notify_sms

    # 다시 올리면 연락처가 있는 학생은 중복으로 건너뛴다
    resp = _upload(admin, content)
    assert "1명을 등록했어요" in resp.text and "건너뛰었어요" in resp.text


def test_import_is_all_or_nothing(admin, db):
    resp = _upload(
        admin,
        _xlsx(
            [
                ["정상", None, None, None, None, None, None],
                [None, "학교만", None, None, None, None, None],
                ["상태오류", None, None, None, "졸업", None, None],
            ]
        ),
    )
    assert "2개 줄에 오류가 있어요" in resp.text
    assert "3" in resp.text and "4" in resp.text
    assert db.exec(select(Student)).first() is None


def test_import_rejects_non_excel(admin):
    resp = _upload(admin, b"not excel")
    assert "엑셀(.xlsx) 파일을 읽을 수 없어요." in resp.text


def test_import_template_download(admin):
    resp = admin.get("/students/import/template.xlsx")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"
