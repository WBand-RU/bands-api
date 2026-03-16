from datetime import datetime, timedelta
from typing import List
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import User, fetch_profile, get_current_user
from app.config import get_settings
from app.database import get_session, init_db
from app.models import Band, BandMember, Invite, InviteStatus, Role

app = FastAPI(title="Bands Service")
settings = get_settings()


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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@app.get("/bands/{band_id}", response_model=schemas.BandOut)
async def get_band(
    band_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    membership = await _get_membership_or_404(session, band_id, user.sub)
    result = await session.execute(select(Band).where(Band.id == band_id))
    band = result.scalar_one_or_none()
    if not band:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Band not found")
    return schemas.BandOut(id=band.id, name=band.name, role=membership.role)


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
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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
    if member.role == Role.owner:
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


@app.get("/bands/{band_id}/invites", response_model=List[schemas.InviteOut])
async def list_invites(
    band_id: uuid.UUID,
    status_filter: InviteStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    stmt = select(Invite).where(Invite.band_id == band_id).order_by(Invite.created_at.desc())
    if status_filter:
        stmt = stmt.where(Invite.status == status_filter)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@app.post("/invites/{token}/accept", response_model=schemas.Message)
async def accept_invite(
    token: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    invite = await session.execute(select(Invite).where(Invite.token == token))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.is_expired:
        invite.mark_expired()
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite expired")
    if invite.status != InviteStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite not active")

    # add member if not exists
    existing = await session.execute(
        select(BandMember).where(BandMember.band_id == invite.band_id, BandMember.user_id == user.sub)
    )
    if existing.scalar_one_or_none():
        invite.status = InviteStatus.accepted
        await session.commit()
        return schemas.Message(message="Already a member")

    member = BandMember(band_id=invite.band_id, user_id=user.sub, role=Role.member)
    session.add(member)
    invite.status = InviteStatus.accepted
    await session.commit()
    return schemas.Message(message="Invite accepted")


@app.post("/invites/{token}/decline", response_model=schemas.Message)
async def decline_invite(
    token: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    invite = await session.execute(select(Invite).where(Invite.token == token))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != InviteStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite not active")
    invite.status = InviteStatus.declined
    await session.commit()
    return schemas.Message(message="Invite declined")


@app.get("/invites/{token}/status", response_model=schemas.InviteOut)
async def invite_status(token: str, session: AsyncSession = Depends(get_session)):
    invite = await session.execute(select(Invite).where(Invite.token == token))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.is_expired:
        invite.mark_expired()
        await session.commit()
    return invite


@app.post("/bands/{band_id}/invites/{invite_id}/revoke", response_model=schemas.Message)
async def revoke_invite(
    band_id: uuid.UUID,
    invite_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    invite = await session.execute(select(Invite).where(Invite.id == invite_id, Invite.band_id == band_id))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    invite.status = InviteStatus.revoked
    await session.commit()
    return schemas.Message(message="Invite revoked")


@app.post("/bands/{band_id}/invites/{invite_id}/resend", response_model=schemas.InviteOut)
async def resend_invite(
    band_id: uuid.UUID,
    invite_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    invite = await session.execute(select(Invite).where(Invite.id == invite_id, Invite.band_id == band_id))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status not in {InviteStatus.pending, InviteStatus.revoked, InviteStatus.expired, InviteStatus.declined}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot resend accepted invite")
    # regenerate token and extend expiration
    invite.token = uuid.uuid4().hex
    invite.status = InviteStatus.pending
    invite.expires_at = datetime.utcnow() + timedelta(days=7)
    await session.commit()
    await session.refresh(invite)
    return invite


@app.post("/bands/{band_id}/transfer-ownership", response_model=schemas.MemberOut)
async def transfer_ownership(
    band_id: uuid.UUID,
    payload: schemas.TransferOwnership,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@app.get("/health", response_model=schemas.Message)
async def health() -> schemas.Message:
    return schemas.Message(message="ok")
