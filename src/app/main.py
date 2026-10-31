from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth, edition
from app.config import BASE_DIR, get_settings
from app.routers import (
    attendance,
    billing,
    classes,
    dashboard,
    deposits,
    notifications,
    public,
    students,
    system,
    teachers,
    webhooks,
)
from app.scheduler import create_scheduler
from app.templating import is_htmx, templates, use_template_dirs


@asynccontextmanager
async def lifespan(app: FastAPI):
    ed = edition.current()
    if ed.on_startup:
        ed.on_startup()
    scheduler = create_scheduler(ed.add_jobs) if get_settings().scheduler_enabled else None
    app.state.scheduler = scheduler  # /healthz에서 확인
    if scheduler:
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app(ed: edition.Edition = edition.BASE) -> FastAPI:
    """ed: 구축판·데모판이 넘기는 판 설정. 테스트는 기본(BASE)."""
    edition.activate(ed)
    settings = get_settings()
    if settings.cookie_secure and settings.secret_key in ("", "change-me"):
        # 운영(HTTPS)인데 비밀키가 기본값이면 세션 쿠키를 위조할 수 있다
        raise RuntimeError(
            "ACADEMY_SECRET_KEY를 설정해 주세요 (운영 환경에서 기본값은 쓸 수 없어요)."
        )
    app = FastAPI(title=settings.academy_name, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.cookie_secure,
        max_age=60 * 60 * 12,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates.env.globals["csrf_token"] = auth.csrf_token
    use_template_dirs(ed.template_dirs)

    app.include_router(auth.router)
    csrf = [Depends(auth.verify_csrf)]
    for r in (dashboard, students, teachers, classes, attendance, billing, deposits, notifications):
        app.include_router(r.router, dependencies=csrf)
    for r in ed.routers:
        app.include_router(r, dependencies=csrf)
    # 외부 호출용: 세션/CSRF 대신 자체 검증
    if ed.allow_webhooks:
        app.include_router(webhooks.router)
    app.include_router(public.router)
    app.include_router(system.router, dependencies=csrf)

    @app.exception_handler(auth.LoginRequired)
    async def _login_required(request: Request, _exc: auth.LoginRequired):
        target = "/login"
        if request.url.path == "/" and ed.landing_path:
            target = ed.landing_path
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    return app
