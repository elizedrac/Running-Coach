# User profile endpoints (name, theme, location, last_synced).
from fastapi import APIRouter, Depends, HTTPException

from db.user_info import get_user_info, set_location, set_name, set_theme
from models.planner import UserInfoRequest
from services.auth import get_current_user

router = APIRouter()


@router.get("/user/info")
def get_info(user_id: str = Depends(get_current_user)):
    try:
        return get_user_info(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/info/name")
def post_name(body: UserInfoRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_name(user_id, body.name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/info/theme")
def post_theme(body: UserInfoRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_theme(user_id, body.theme)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/info/location")
def post_location(body: UserInfoRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_location(user_id, body.location)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
