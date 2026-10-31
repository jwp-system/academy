"""데모판 명령: demo serve | reset"""

import argparse

from demo import prepare_env


def main() -> None:
    prepare_env()
    from app import cli

    parser = argparse.ArgumentParser(prog="demo", description="학원 관리 시스템 (데모판)")
    sub = parser.add_subparsers(required=True)
    cli.add_serve(sub, "demo.main:app")

    p = sub.add_parser("reset", help="데모 데이터를 처음 상태로")

    def run_reset(_args):
        from demo.seed import reset

        reset()
        print("데모 데이터를 처음 상태로 되돌렸어요.")

    p.set_defaults(func=run_reset)
    cli.run(parser)
