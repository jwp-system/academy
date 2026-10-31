import os
import re
import tempfile

# academy 모듈을 import하기 전에 테스트용 DB와 설정을 지정한다
_tmp = tempfile.mkdtemp()
os.environ["ACADEMY_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ACADEMY_SCHEDULER_ENABLED"] = "false"
os.environ["ACADEMY_SMS_WEBHOOK_TOKEN"] = "test-token"
# .env에 게이트웨이·메일 접속 정보가 있어도 테스트 중에는 절대 실제로 보내지 않는다
os.environ["ACADEMY_SMS_MODE"] = "test"
os.environ["ACADEMY_EMAIL_MODE"] = "test"
os.environ["ACADEMY_ACADEMY_NAME"] = "테스트학원"
os.environ["ACADEMY_BANK_ACCOUNT"] = "OO은행 123-456"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Role, User  # noqa: E402


@pytest.fixture(autouse=True)
def db():
    from app.services.notify.channels import TEST

    TEST.fail = False
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def get_csrf(client: TestClient) -> str:
    html = client.get("/login").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture
def make_user(db):
    def _make(email="admin@example.com", password="pw1234", role=Role.admin):
        user = User(name="테스트", email=email, password_hash=hash_password(password), role=role)
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def login(client, make_user):
    def _login(role=Role.admin):
        make_user(email=f"{role}@example.com", role=role)
        token = get_csrf(client)
        resp = client.post(
            "/login",
            data={"email": f"{role}@example.com", "password": "pw1234", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        # 로그인 시 세션이 초기화되어 CSRF 토큰도 바뀌므로, 로그인 후 페이지에서 새 토큰을 읽는다
        html = client.get("/").text
        return re.search(r'"X-CSRF-Token": "([^"]+)"', html).group(1)

    return _login
