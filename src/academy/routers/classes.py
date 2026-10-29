from fastapi import APIRouter, Request

from academy.auth import CurrentUser
from academy.templating import render

router = APIRouter(prefix="/classes")


@router.get("")
def index(request: Request, user: CurrentUser):
    return render(request, "placeholder.html", {"user": user, "title": "반·시간표", "week": "3"})
