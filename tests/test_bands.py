import uuid

import pytest

from app.models import BandMember, Role


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_open(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["message"] == "ok"


@pytest.mark.asyncio
async def test_create_and_list_bands(client, token_factory):
    token = token_factory("user1")
    res = await client.post("/bands", json={"name": "MyBand"}, headers=_auth(token))
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["name"] == "MyBand"
    assert data["role"] == Role.owner.value

    res = await client.get("/bands", headers=_auth(token))
    assert res.status_code == 200
    bands = res.json()
    assert len(bands) == 1
    assert bands[0]["role"] == Role.owner.value


@pytest.mark.asyncio
async def test_rename_band(client, token_factory):
    token = token_factory("user1")
    create = await client.post("/bands", json={"name": "Old"}, headers=_auth(token))
    band_id = create.json()["id"]

    res = await client.patch(
        f"/bands/{band_id}/rename",
        json={"name": "New"},
        headers=_auth(token),
    )
    assert res.status_code == 200
    assert res.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_band_owner_only(client, token_factory):
    owner_token = token_factory("owner")
    member_token = token_factory("member")

    create = await client.post("/bands", json={"name": "Del"}, headers=_auth(owner_token))
    band_id = create.json()["id"]

    res = await client.delete(f"/bands/{band_id}", headers=_auth(member_token))
    assert res.status_code == 403

    res = await client.delete(f"/bands/{band_id}", headers=_auth(owner_token))
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_members_listing_and_removal(client, session, token_factory):
    owner_token = token_factory("owner")
    other_token = token_factory("other")

    create = await client.post("/bands", json={"name": "Members"}, headers=_auth(owner_token))
    band_id = create.json()["id"]

    # Seed another member directly in DB
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="other", role=Role.member))
    await session.commit()

    res = await client.get(f"/bands/{band_id}/members", headers=_auth(owner_token))
    assert res.status_code == 200
    members = res.json()
    assert {m["user_id"] for m in members} == {"owner", "other"}

    # Non-owner (member) cannot remove; owner can remove member (not self)
    res = await client.delete(
        f"/bands/{band_id}/members/owner",
        headers=_auth(other_token),
    )
    assert res.status_code == 403

    res = await client.delete(
        f"/bands/{band_id}/members/other",
        headers=_auth(owner_token),
    )
    assert res.status_code == 200

    res = await client.get(f"/bands/{band_id}/members", headers=_auth(owner_token))
    assert {m["user_id"] for m in res.json()} == {"owner"}


@pytest.mark.asyncio
async def test_update_member_role_owner_only(client, session, token_factory):
    owner_token = token_factory("owner")
    admin_token = token_factory("admin")
    res = await client.post("/bands", json={"name": "Roles"}, headers=_auth(owner_token))
    band_id = res.json()["id"]

    # add member for update
    member_id = "member1"
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id=member_id, role=Role.member))
    await session.commit()

    # admin (non-owner) cannot change role
    res = await client.put(
        f"/bands/{band_id}/members/{member_id}/role",
        json={"role": Role.admin.value},
        headers=_auth(admin_token),
    )
    assert res.status_code == 404 or res.status_code == 403

    # owner can promote member to admin
    res = await client.put(
        f"/bands/{band_id}/members/{member_id}/role",
        json={"role": Role.admin.value},
        headers=_auth(owner_token),
    )
    assert res.status_code == 200
    assert res.json()["role"] == Role.admin.value

    # cannot assign owner role
    res = await client.put(
        f"/bands/{band_id}/members/{uuid.uuid4()}/role",
        json={"role": Role.owner.value},
        headers=_auth(owner_token),
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_create_invite_admin_or_owner(client, token_factory):
    owner_token = token_factory("owner")
    res = await client.post("/bands", json={"name": "Inv"}, headers=_auth(owner_token))
    band_id = res.json()["id"]

    res = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "test@example.com"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 201
    assert res.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_requires_auth_returns_401(client):
    res = await client.get("/bands")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_band_name_returns_400(client, token_factory):
    token = token_factory("u1")
    res = await client.post("/bands", json={"name": "Dup"}, headers=_auth(token))
    assert res.status_code == 201
    res = await client.post("/bands", json={"name": "Dup"}, headers=_auth(token))
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_rename_forbidden_for_member(client, token_factory, session):
    owner = token_factory("owner2")
    member = token_factory("member2")
    create = await client.post("/bands", json={"name": "Protected"}, headers=_auth(owner))
    band_id = create.json()["id"]

    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="member2", role=Role.member))
    await session.commit()

    res = await client.patch(
        f"/bands/{band_id}/rename",
        json={"name": "Nope"},
        headers=_auth(member),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_invite_duplicate_email_returns_400(client, token_factory):
    owner = token_factory("owner3")
    create = await client.post("/bands", json={"name": "InvDup"}, headers=_auth(owner))
    band_id = create.json()["id"]

    first = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "dup@example.com"},
        headers=_auth(owner),
    )
    assert first.status_code == 201

    second = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "dup@example.com"},
        headers=_auth(owner),
    )
    assert second.status_code == 400
