from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app.edition import Edition
from demo import seed
from demo.routes import router

HERE = Path(__file__).parent
RESET_HOUR = 4


def _add_jobs(scheduler: BackgroundScheduler) -> None:
    scheduler.add_job(seed.reset, "cron", hour=RESET_HOUR, id="demo_reset", max_instances=1)


def build() -> Edition:
    return Edition(
        key="demo",
        label="데모",
        allow_live_notifications=False,  # 어떤 설정이 들어와도 실제 발송 금지
        allow_webhooks=False,
        template_dirs=[HERE / "templates"],
        routers=[router],
        add_jobs=_add_jobs,
        on_startup=seed.ensure_seeded,
    )
