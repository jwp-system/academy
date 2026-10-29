from fastapi import APIRouter, Request

from academy.auth import CurrentUser
from academy.templating import render

router = APIRouter(prefix="/attendance")


@router.get("")
def index(request: Request, user: CurrentUser):
    return render(request, "placeholder.html", {"user": user, "title": "출결 체크", "week": "4"})
