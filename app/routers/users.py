"""Router for user profile endpoints."""

from fastapi import APIRouter, Depends

from app import schemas
from app.auth import User, get_current_user

router = APIRouter()


@router.get("/me", response_model=schemas.User)
async def get_me(user: User = Depends(get_current_user)):
    """Get current user profile."""
    return user
