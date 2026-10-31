"""백업 화면 (원장): 자동 백업 폴더, 최근 사본, 지금 백업, 내려받기."""

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.auth import AdminUser
from app.config import get_settings
from app.services import backup
from app.templating import hx_trigger, render

router = APIRouter(prefix="/backups")


def _files() -> list[dict]:
    folder = Path(get_settings().backup_dir)
    out = []
    for f in sorted(folder.glob("academy-*.db"), reverse=True)[:60]:
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "at": datetime.fromtimestamp(st.st_mtime)})
    return out


@router.get("")
def index(request: Request, user: AdminUser):
    s = get_settings()
    files = _files()
    stale = not files or (datetime.now() - files[0]["at"]).days >= 2
    return render(
        request,
        "backups.html",
        {
            "user": user,
            "folder": s.backup_dir,
            "keep": s.backup_keep_days,
            "files": files,
            "stale": stale,
        },
        partial="_backup_list.html",
    )


@router.post("/run")
def run_now(user: AdminUser):
    made = backup.run_scheduled()
    return hx_trigger("backupsChanged", toast=f"{made.name}을 만들었어요")


@router.get("/{name}")
def download(user: AdminUser, name: str):
    folder = Path(get_settings().backup_dir).resolve()
    path = (folder / name).resolve()
    # 백업 폴더 밖의 파일은 절대 내주지 않는다
    if path.parent != folder or not path.name.startswith("academy-") or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"},
    )
