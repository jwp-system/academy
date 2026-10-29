# academy

소규모 학원 관리 시스템 (FastAPI + Jinja2 + HTMX + SQLite). 기획안은 [academy.md](academy.md).

## 시작하기

```bash
uv sync
cp .env.example .env              # ACADEMY_SECRET_KEY 등 채우기
uv run alembic upgrade head       # data/academy.db 생성
uv run academy create-admin admin@example.com --name 원장
uv run academy serve --reload     # http://127.0.0.1:8000
```

## 구조

```
src/academy/
├─ main.py          앱 생성, 미들웨어, 라우터 등록, 스케줄러 시작
├─ config.py        설정 (.env, ACADEMY_ 접두사)
├─ db.py            엔진, 세션, SQLite PRAGMA(WAL, foreign_keys)
├─ models.py        SQLModel 모델
├─ auth.py          로그인, CSRF, require_role
├─ templating.py    render(): HX-Request면 partial, 아니면 전체 페이지
├─ scheduler.py     APScheduler 작업
├─ routers/         화면별 라우터 (webhooks, public은 세션/CSRF 미적용)
├─ services/        회차·청구 생성, 입금 매칭, 알림 outbox와 채널 어댑터, 엑셀
├─ templates/       base.html, 화면별 전체 페이지와 _partial 조각
└─ static/          htmx, alpine, fullcalendar (로컬 보관), css/app.css 디자인 시스템, js/app.js(모달·토스트·확인창)
alembic/            마이그레이션
data/               SQLite DB (커밋 안 함)
```

## 개발

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
uv run alembic revision --autogenerate -m "..."   # 모델 변경 후
```
