from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from academy.backups import router as backups_router
from app.edition import Edition
from app.services import backup

HERE = Path(__file__).parent


def _add_jobs(scheduler: BackgroundScheduler) -> None:
    # 매일 새벽 2시 30분 자동 백업 (PC가 꺼져 있었다면 켜진 뒤 1시간 안에 한 번 따라잡음)
    scheduler.add_job(
        backup.run_scheduled,
        "cron",
        hour=2,
        minute=30,
        id="backup",
        misfire_grace_time=3600,
        coalesce=True,
    )


def build() -> Edition:
    return Edition(
        key="academy",
        template_dirs=[HERE / "templates"],
        routers=[backups_router],
        nav_admin=[("/backups", "백업", "download")],
        add_jobs=_add_jobs,
    )
