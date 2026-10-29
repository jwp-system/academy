from fastapi import APIRouter, Request

from academy.auth import AdminUser
from academy.templating import render

router = APIRouter(prefix="/notifications")


@router.get("")
def index(request: Request, user: AdminUser):
    return render(request, "placeholder.html", {"user": user, "title": "알림", "week": "6"})
