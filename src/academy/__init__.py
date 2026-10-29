import argparse
import getpass


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("academy.main:app", host=args.host, port=args.port, reload=args.reload)


def _create_admin(args: argparse.Namespace) -> None:
    from sqlmodel import Session, select

    from academy.auth import hash_password
    from academy.db import engine
    from academy.models import Role, User

    email = args.email.strip().lower()
    password = getpass.getpass("비밀번호: ")
    if password != getpass.getpass("비밀번호 확인: "):
        raise SystemExit("비밀번호가 일치하지 않습니다.")
    with Session(engine) as db:
        if db.exec(select(User).where(User.email == email)).first():
            raise SystemExit(f"이미 존재하는 이메일입니다: {email}")
        db.add(
            User(
                name=args.name, email=email, password_hash=hash_password(password), role=Role.admin
            )
        )
        db.commit()
    print(f"원장 계정을 만들었습니다: {email}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="academy")
    sub = parser.add_subparsers(required=True)

    serve = sub.add_parser("serve", help="웹 서버 실행")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    admin = sub.add_parser("create-admin", help="원장 계정 생성")
    admin.add_argument("email")
    admin.add_argument("--name", default="원장")
    admin.set_defaults(func=_create_admin)

    args = parser.parse_args()
    args.func(args)
