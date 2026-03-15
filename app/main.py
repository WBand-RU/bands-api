from typing import List
import uuid

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import User, get_current_user
from app.database import get_session, init_db
from app.models import Band, BandMember, Invite, InviteStatus, Role

app = FastAPI(title="Bands Service")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


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


@app.get("/bands", response_model=List[schemas.BandOut])
async def list_bands(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(Band, BandMember.role)
        .join(BandMember, Band.id == BandMember.band_id)
        .where(BandMember.user_id == user.sub)
    )
    bands = []
    for band, role in result.all():
        bands.append(schemas.BandOut(id=band.id, name=band.name, role=role))
    return bands


@app.post("/bands", response_model=schemas.BandOut, status_code=status.HTTP_201_CREATED)
async def create_band(
    payload: schemas.BandCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@app.patch("/bands/{band_id}/rename", response_model=schemas.BandOut)
async def rename_band(
    band_id: uuid.UUID,
    payload: schemas.BandRename,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@app.delete("/bands/{band_id}", response_model=schemas.Message)
async def delete_band(
    band_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@app.get("/bands/{band_id}/members", response_model=List[schemas.MemberOut])
async def list_members(
    band_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _get_membership_or_404(session, band_id, user.sub)
    result = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id).order_by(BandMember.created_at)
    )
    members = result.scalars().all()
    return [
        schemas.MemberOut(
            id=m.id, user_id=m.user_id, role=m.role, created_at=m.created_at
        )
        for m in members
    ]


@app.delete("/bands/{band_id}/members/{member_user_id}", response_model=schemas.Message)
async def remove_member(
    band_id: uuid.UUID,
    member_user_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    result = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == member_user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if member.role == Role.owner and membership.role != Role.owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot remove owner")

    await session.delete(member)
    await session.commit()
    return schemas.Message(message="Member removed")


@app.put("/bands/{band_id}/members/{member_user_id}/role", response_model=schemas.MemberOut)
async def update_member_role(
    band_id: uuid.UUID,
    member_user_id: str,
    payload: schemas.RoleUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@app.post("/bands/{band_id}/invites", response_model=schemas.InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(
    band_id: uuid.UUID,
    payload: schemas.InviteCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    invite = Invite(band_id=band_id, email=payload.email)
    session.add(invite)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite already exists")
    await session.refresh(invite)
    return invite


@app.get("/health", response_model=schemas.Message)
async def health() -> schemas.Message:
    return schemas.Message(message="ok")
