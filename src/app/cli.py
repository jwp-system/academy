"""구축판·데모판 명령줄 도구가 함께 쓰는 명령."""

import argparse
import getpass

from app.config import PROJECT_DIR


def add_serve(sub, app_path: str, default_host: str = "127.0.0.1") -> None:
    p = sub.add_parser("serve", help="웹 서버 실행 (반드시 프로세스 하나로)")
    p.add_argument("--host", default=default_host)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="코드가 바뀌면 다시 시작 (개발용)")

    def run(args):
        import uvicorn

        uvicorn.run(app_path, host=args.host, port=args.port, reload=args.reload)

    p.set_defaults(func=run)


def add_create_admin(sub) -> None:
    p = sub.add_parser("create-admin", help="원장 계정 만들기")
    p.add_argument("email")
    p.add_argument("--name", default="원장")

    def run(args):
        from sqlmodel import Session, select

        from app.auth import hash_password
        from app.db import engine
        from app.models import Role, User

        email = args.email.strip().lower()
        password = getpass.getpass("비밀번호: ")
        if len(password) < 8:
            raise SystemExit("비밀번호는 8자 이상이어야 해요.")
        if password != getpass.getpass("비밀번호 확인: "):
            raise SystemExit("비밀번호가 일치하지 않아요.")
        with Session(engine) as db:
            if db.exec(select(User).where(User.email == email)).first():
                raise SystemExit(f"이미 있는 이메일이에요: {email}")
            db.add(
                User(
                    name=args.name,
                    email=email,
                    password_hash=hash_password(password),
                    role=Role.admin,
                )
            )
            db.commit()
        print(f"원장 계정을 만들었어요: {email}")

    p.set_defaults(func=run)


def add_migrate(sub) -> None:
    p = sub.add_parser("migrate", help="DB 구조를 최신으로 (alembic upgrade head)")

    def run(_args):
        from alembic.config import Config

        from alembic import command

        cfg = Config(str(PROJECT_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_DIR / "alembic"))
        command.upgrade(cfg, "head")
        print("DB 구조를 최신으로 맞췄어요.")

    p.set_defaults(func=run)


def run(parser: argparse.ArgumentParser) -> None:
    args = parser.parse_args()
    args.func(args)
