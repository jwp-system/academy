"""구축판 명령: academy serve | create-admin | migrate | backup"""

import argparse

from app import cli


def main() -> None:
    parser = argparse.ArgumentParser(prog="academy", description="학원 관리 시스템 (구축판)")
    sub = parser.add_subparsers(required=True)
    cli.add_serve(sub, "academy.main:app")
    cli.add_create_admin(sub)
    cli.add_migrate(sub)

    p = sub.add_parser("backup", help="지금 DB 사본 만들기 (백업 폴더)")

    def run_backup(_args):
        from app.services.backup import run_scheduled

        print(f"백업을 만들었어요: {run_scheduled()}")

    p.set_defaults(func=run_backup)
    cli.run(parser)
