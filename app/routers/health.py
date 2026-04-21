"""Router for health check endpoints."""

from fastapi import APIRouter

from app import schemas

router = APIRouter()


@router.get("/health", response_model=schemas.Message)
async def health() -> schemas.Message:
    """Health check endpoint."""
    return schemas.Message(message="ok")
