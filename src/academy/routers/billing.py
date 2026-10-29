from fastapi import APIRouter, Request

from academy.auth import AdminUser
from academy.templating import render

router = APIRouter(prefix="/billing")


@router.get("")
def index(request: Request, user: AdminUser):
    return render(
        request, "placeholder.html", {"user": user, "title": "수납·입금 매칭", "week": "5, 7"}
    )
