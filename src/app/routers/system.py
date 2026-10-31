"""운영용: 상태 확인(/healthz, 로그인 없이)과 DB 백업 받기(원장)."""

import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.background import BackgroundTask

from app.auth import AdminUser
from app.db import DBSession
from app.services.backup import NotSqlite, snapshot

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request, db: DBSession):
    """업타임 모니터링용. DB가 응답하고 스케줄러가 돌고 있으면 200."""
    try:
        db.exec(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    scheduler = getattr(request.app.state, "scheduler", None)
    sched = "disabled" if scheduler is None else ("running" if scheduler.running else "stopped")
    ok = db_ok and sched != "stopped"
    return JSONResponse(
        {"status": "ok" if ok else "fail", "db": db_ok, "scheduler": sched},
        status_code=200 if ok else 503,
    )


@router.get("/system/backup")
def backup(user: AdminUser):
    """지금 DB 사본을 내려받는다."""
    tmp = Path(tempfile.mkstemp(prefix="academy-backup-", suffix=".db")[1])
    try:
        snapshot(tmp)
    except NotSqlite:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="SQLite DB만 내려받을 수 있어요.") from None
    name = f"academy-{datetime.now():%Y%m%d-%H%M}.db"
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
        background=BackgroundTask(tmp.unlink, missing_ok=True),
    )
