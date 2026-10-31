import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.templating import Jinja2Templates
from jinja2 import FileSystemLoader
from starlette.datastructures import FormData as StarletteFormData

from app.config import BASE_DIR, get_settings
from app.edition import current as current_edition
from app.services.notify.channels import any_test_mode
from app.services.sessions import class_color

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["edition"] = current_edition


def use_template_dirs(dirs: list[Path]) -> None:
    """판 전용 템플릿 폴더를 공통 템플릿보다 먼저 찾게 한다 (같은 이름이면 판 쪽이 이긴다)."""
    templates.env.loader = FileSystemLoader([*map(str, dirs), str(BASE_DIR / "templates")])


templates.env.globals["academy_name"] = get_settings().academy_name
templates.env.globals["class_color"] = class_color
templates.env.globals["notify_test_mode"] = any_test_mode
templates.env.globals["STATUS_LABELS"] = {"active": "재원", "paused": "휴원", "withdrawn": "퇴원"}


def _phone(value: str | None) -> str:
    """01012345678 → 010-1234-5678, 0212345678 → 02-1234-5678"""
    if not value or len(value) < 9:
        return value or ""
    head = 2 if value.startswith("02") else 3
    return f"{value[:head]}-{value[head:-4]}-{value[-4:]}"


templates.env.filters["phone"] = _phone


def _hm(value: Any) -> str:
    """time → "16:00" (input type=time 값). 이미 문자열이면 그대로."""
    return value.strftime("%H:%M") if hasattr(value, "strftime") else (value or "")


templates.env.filters["hm"] = _hm
templates.env.filters["won"] = lambda v: f"{v:,}원" if isinstance(v, int) else ""


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


async def _form_data(request: Request) -> dict[str, Any]:
    return dict(await request.form())


# 폼 본문을 dict로 받는다. 엔드포인트는 sync로 두고 DB 작업을 스레드풀에서 돌리기 위해 의존성으로 분리.
FormData = Annotated[dict[str, Any], Depends(_form_data)]


async def _multi_form(request: Request) -> StarletteFormData:
    return await request.form()


# 같은 이름이 여러 번 오는 폼(체크박스 여러 개)은 getlist가 필요해서 원본 FormData로 받는다.
MultiFormData = Annotated[StarletteFormData, Depends(_multi_form)]


def trigger_headers(*events: str, toast: str | None = None) -> dict[str, str]:
    """HX-Trigger 헤더. toast를 주면 화면 하단에 알림을 띄운다 (static/js/app.js)."""
    payload: dict[str, Any] = dict.fromkeys(events)
    if toast:
        payload["toast"] = toast
    # 헤더는 latin-1만 허용되므로 한글은 \uXXXX로 이스케이프한다 (htmx가 JSON으로 해석)
    return {"HX-Trigger": json.dumps(payload)}


def hx_trigger(*events: str, toast: str | None = None) -> Response:
    """빈 응답 + HX-Trigger. 모달 폼 저장 성공 시 대화상자를 닫고 목록을 새로고침하게 한다."""
    return Response(status_code=200, headers=trigger_headers(*events, toast=toast))


def render(
    request: Request,
    page: str,
    context: dict[str, Any] | None = None,
    *,
    partial: str | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
):
    """HX-Request 헤더가 있으면 부분 조각(partial)을, 없으면 전체 페이지를 렌더링한다 (8절)."""
    name = partial if partial and is_htmx(request) else page
    return templates.TemplateResponse(
        request, name, context or {}, status_code=status_code, headers=headers
    )
