import json
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.templating import Jinja2Templates

from academy.config import BASE_DIR, get_settings

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["academy_name"] = get_settings().academy_name
templates.env.globals["STATUS_LABELS"] = {"active": "재원", "paused": "휴원", "withdrawn": "퇴원"}


def _phone(value: str | None) -> str:
    """01012345678 → 010-1234-5678, 0212345678 → 02-1234-5678"""
    if not value or len(value) < 9:
        return value or ""
    head = 2 if value.startswith("02") else 3
    return f"{value[:head]}-{value[head:-4]}-{value[-4:]}"


templates.env.filters["phone"] = _phone


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


async def _form_data(request: Request) -> dict[str, Any]:
    return dict(await request.form())


# 폼 본문을 dict로 받는다. 엔드포인트는 sync로 두고 DB 작업을 스레드풀에서 돌리기 위해 의존성으로 분리.
FormData = Annotated[dict[str, Any], Depends(_form_data)]


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
