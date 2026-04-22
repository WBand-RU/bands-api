"""Router for band member management endpoints."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import User, fetch_profile, get_current_user
from app.config import get_settings
from app.database import get_session
from app.models import BandMember, Role

router = APIRouter()
settings = get_settings()


async def _get_membership_or_404(
    session: AsyncSession, band_id: uuid.UUID, user_id: str
) -> BandMember:
    result = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Band not found")
    return member


def _ensure_admin_or_owner(member: BandMember) -> None:
    if member.role not in {Role.owner, Role.admin}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient rights")


@router.get("/{band_id}/members", response_model=List[schemas.MemberOut])
async def list_members(
    band_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """List all members of a band."""
    await _get_membership_or_404(session, band_id, user.sub)
    result = await session.execute(
        select(BandMember)
        .where(BandMember.band_id == band_id)
        .order_by(BandMember.created_at)
        .offset(offset)
        .limit(limit)
    )
    members = result.scalars().all()
    enriched = []
    for m in members:
        profile = await fetch_profile(m.user_id) if not settings.auth_disable_verification else {}
        enriched.append(
            schemas.MemberOut(
                id=m.id,
                user_id=m.user_id,
                role=m.role,
                created_at=m.created_at,
                name=profile.get("name"),
                email=profile.get("email"),
            )
        )
    return enriched


@router.delete("/{band_id}/members/{member_user_id}", response_model=schemas.Message)
async def remove_member(
    band_id: uuid.UUID,
    member_user_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Remove a member from the band (admin/owner only)."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    result = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == member_user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == Role.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot remove owner")

    await session.delete(member)
    await session.commit()
    return schemas.Message(message="Member removed")


@router.put("/{band_id}/members/{member_user_id}/role", response_model=schemas.MemberOut)
async def update_member_role(
    band_id: uuid.UUID,
    member_user_id: str,
    payload: schemas.RoleUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Update a member's role (owner only)."""
    if payload.role == Role.owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot assign owner role")

    membership = await _get_membership_or_404(session, band_id, user.sub)
    if membership.role != Role.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner rights required")

    result = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == member_user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == Role.owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change owner role")

    member.role = payload.role
    await session.commit()
    await session.refresh(member)
    return schemas.MemberOut(id=member.id, user_id=member.user_id, role=member.role, created_at=member.created_at)
