import os
import re
import tempfile

# academy 모듈을 import하기 전에 테스트용 DB와 설정을 지정한다
_tmp = tempfile.mkdtemp()
os.environ["ACADEMY_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ACADEMY_SCHEDULER_ENABLED"] = "false"
os.environ["ACADEMY_SMS_WEBHOOK_TOKEN"] = "test-token"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

from academy.auth import hash_password  # noqa: E402
from academy.db import engine  # noqa: E402
from academy.main import app  # noqa: E402
from academy.models import Role, User  # noqa: E402


@pytest.fixture(autouse=True)
def db():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(app) as c:
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
