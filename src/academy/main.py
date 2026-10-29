from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from academy import auth
from academy.config import BASE_DIR, get_settings
from academy.routers import (
    attendance,
    billing,
    classes,
    dashboard,
    notifications,
    public,
    students,
    teachers,
    webhooks,
)
from academy.scheduler import create_scheduler
from academy.templating import is_htmx, templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler() if get_settings().scheduler_enabled else None
    if scheduler:
        scheduler.start()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
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

    app.include_router(auth.router)
    csrf = [Depends(auth.verify_csrf)]
    for r in (dashboard, students, teachers, classes, attendance, billing, notifications):
        app.include_router(r.router, dependencies=csrf)
    # 외부 호출용: 세션/CSRF 대신 자체 검증
    app.include_router(webhooks.router)
    app.include_router(public.router)

    @app.exception_handler(auth.LoginRequired)
    async def _login_required(request: Request, _exc: auth.LoginRequired):
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    return app


app = create_app()
