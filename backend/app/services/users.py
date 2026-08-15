"""User accounts stored as :User nodes in Neo4j."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.services import security

ROLES = {"admin", "user"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "verified": bool(row.get("verified")),
    }


async def get_by_email(email: str) -> dict | None:
    rows = await neo4j_client.run(
        """
        MATCH (u:User {email: $email})
        RETURN u.id AS id, u.name AS name, u.email AS email, u.role AS role,
               u.password_hash AS password_hash, u.verified AS verified,
               u.otp_hash AS otp_hash, u.otp_expires_at AS otp_expires_at
        """,
        email=email.strip().lower(),
    )
    return rows[0] if rows else None


async def get_by_id(user_id: str) -> dict | None:
    rows = await neo4j_client.run(
        """
        MATCH (u:User {id: $id})
        RETURN u.id AS id, u.name AS name, u.email AS email, u.role AS role,
               u.verified AS verified
        """,
        id=user_id,
    )
    return rows[0] if rows else None


async def upsert_pending(
    *, name: str, email: str, password: str, role: str
) -> tuple[dict, str]:
    email = email.strip().lower()
    otp = security.new_otp()
    expires = (_now() + timedelta(minutes=settings.otp_expire_minutes)).isoformat()
    existing = await get_by_email(email)
    if existing and existing.get("verified"):
        raise ValueError("An account with this email already exists")

    password_hash = security.hash_password(password)
    otp_hash = security.hash_otp(otp)
    user_id = existing["id"] if existing else str(uuid.uuid4())

    await neo4j_client.run_write(
        """
        MERGE (u:User {email: $email})
        ON CREATE SET u.id = $id, u.created_at = datetime()
        SET u.name = $name,
            u.role = $role,
            u.password_hash = $password_hash,
            u.verified = false,
            u.otp_hash = $otp_hash,
            u.otp_expires_at = $otp_expires_at,
            u.updated_at = datetime()
        RETURN u.id AS id
        """,
        email=email,
        id=user_id,
        name=name.strip(),
        role=role,
        password_hash=password_hash,
        otp_hash=otp_hash,
        otp_expires_at=expires,
    )
    return {
        "id": user_id,
        "name": name.strip(),
        "email": email,
        "role": role,
        "verified": False,
    }, otp


async def set_otp(email: str) -> str:
    user = await get_by_email(email)
    if user is None:
        raise ValueError("No account for this email")
    if user.get("verified"):
        raise ValueError("This account is already verified")
    otp = security.new_otp()
    expires = (_now() + timedelta(minutes=settings.otp_expire_minutes)).isoformat()
    await neo4j_client.run_write(
        """
        MATCH (u:User {email: $email})
        SET u.otp_hash = $otp_hash, u.otp_expires_at = $otp_expires_at, u.updated_at = datetime()
        """,
        email=email.strip().lower(),
        otp_hash=security.hash_otp(otp),
        otp_expires_at=expires,
    )
    return otp


async def verify_otp(email: str, otp: str) -> dict:
    user = await get_by_email(email)
    if user is None:
        raise ValueError("No account for this email")
    if user.get("verified"):
        return user
    expires = user.get("otp_expires_at")
    if expires is None:
        raise ValueError("OTP has expired. Request a new code.")
    if hasattr(expires, "to_native"):
        exp_dt = expires.to_native()
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    else:
        try:
            exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError("OTP has expired. Request a new code.") from exc
    if exp_dt < _now():
        raise ValueError("OTP has expired. Request a new code.")
    if not security.otp_matches(otp.strip(), user.get("otp_hash") or ""):
        raise ValueError("Invalid OTP")
    await neo4j_client.run_write(
        """
        MATCH (u:User {email: $email})
        SET u.verified = true, u.otp_hash = null, u.otp_expires_at = null, u.updated_at = datetime()
        """,
        email=email.strip().lower(),
    )
    user["verified"] = True
    return user
