"""Router for band management endpoints."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import User, get_current_user
from app.database import get_session
from app.models import Band, BandMember, Role

router = APIRouter()


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


@router.get("", response_model=List[schemas.BandOut])
async def list_bands(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """List all bands the current user is a member of."""
    result = await session.execute(
        select(Band, BandMember.role)
        .join(BandMember, Band.id == BandMember.band_id)
        .where(BandMember.user_id == user.sub)
        .offset(offset)
        .limit(limit)
    )
    bands = []
    for band, role in result.all():
        bands.append(schemas.BandOut(id=band.id, name=band.name, role=role))
    return bands


@router.get("/check-name", response_model=schemas.BandCheckName)
async def check_band_name(
    name: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Check if a band name is available."""
    result = await session.execute(select(Band).where(Band.name == name))
    band = result.scalar_one_or_none()
    return schemas.BandCheckName(name=name, available=band is None)


@router.get("/{band_id}", response_model=schemas.BandOut)
async def get_band(
    band_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Get a specific band by ID."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    result = await session.execute(select(Band).where(Band.id == band_id))
    band = result.scalar_one_or_none()
    if not band:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Band not found")
    return schemas.BandOut(id=band.id, name=band.name, role=membership.role)


@router.post("", response_model=schemas.BandOut, status_code=status.HTTP_201_CREATED)
async def create_band(
    payload: schemas.BandCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Create a new band."""
    band = Band(name=payload.name)
    member = BandMember(user_id=user.sub, role=Role.owner)
    band.members.append(member)
    session.add(band)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Band name already exists")
    await session.refresh(member)
    return schemas.BandOut(id=band.id, name=band.name, role=member.role)


@router.patch("/{band_id}/rename", response_model=schemas.BandOut)
async def rename_band(
    band_id: uuid.UUID,
    payload: schemas.BandRename,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Rename a band (admin/owner only)."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    result = await session.execute(select(Band).where(Band.id == band_id))
    band = result.scalar_one_or_none()
    if not band:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Band not found")

    band.name = payload.name
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Band name already exists")
    await session.refresh(band)
    return schemas.BandOut(id=band.id, name=band.name, role=membership.role)


@router.delete("/{band_id}", response_model=schemas.Message)
async def delete_band(
    band_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Delete a band (owner only)."""
    result = await session.execute(select(Band).where(Band.id == band_id))
    band = result.scalar_one_or_none()
    if not band:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Band not found")

    membership = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == user.sub)
    )
    membership = membership.scalar_one_or_none()
    if not membership or membership.role != Role.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner rights required")

    await session.delete(band)
    await session.commit()
    return schemas.Message(message="Band deleted")


@router.post("/{band_id}/leave", response_model=schemas.Message)
async def leave_band(
    band_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Leave a band (not allowed for owner)."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    if membership.role == Role.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner cannot leave; transfer ownership first",
        )
    await session.delete(membership)
    await session.commit()
    return schemas.Message(message="You have left the band")


@router.post("/{band_id}/transfer-ownership", response_model=schemas.MemberOut)
async def transfer_ownership(
    band_id: uuid.UUID,
    payload: schemas.TransferOwnership,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Transfer band ownership to another member."""
    current = await _get_membership_or_404(session, band_id, user.sub)
    if current.role != Role.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner rights required")

    target = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == payload.new_owner_user_id)
    )
    target = target.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target member not found")

    current.role = Role.admin
    target.role = Role.owner
    await session.commit()
    await session.refresh(target)
    return schemas.MemberOut(id=target.id, user_id=target.user_id, role=target.role, created_at=target.created_at)
