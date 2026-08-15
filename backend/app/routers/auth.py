from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.config import settings
from app.db.neo4j_client import neo4j_client
from app.deps import get_current_user
from app.services import security, users

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="user")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class OtpRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6)


class ResendRequest(BaseModel):
    email: EmailStr


def _require_graph() -> None:
    if not neo4j_client.is_connected:
        raise HTTPException(503, "Neo4j is not connected")


def _token_payload(user: dict) -> dict:
    token = security.issue_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
        name=user["name"],
    )
    return {"token": token, "token_type": "bearer", "user": users.public_user(user)}


@router.post("/signup")
async def signup(payload: SignupRequest) -> dict:
    _require_graph()
    role = payload.role.strip().lower()
    if role not in users.ROLES:
        raise HTTPException(400, "Role must be admin or user")
    try:
        account, otp = await users.upsert_pending(
            name=payload.name,
            email=str(payload.email),
            password=payload.password,
            role=role,
        )
        security.send_otp_email(account["email"], otp, account["name"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "status": "otp_sent",
        "email": account["email"],
        "message": "Enter the 6-digit code sent to your email to finish signup.",
    }


@router.post("/verify-otp")
async def verify_otp(payload: OtpRequest) -> dict:
    _require_graph()
    try:
        user = await users.verify_otp(str(payload.email), payload.otp)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _token_payload(user)


@router.post("/resend-otp")
async def resend_otp(payload: ResendRequest) -> dict:
    _require_graph()
    try:
        user = await users.get_by_email(str(payload.email))
        if user is None:
            raise ValueError("No account for this email")
        otp = await users.set_otp(str(payload.email))
        security.send_otp_email(user["email"], otp, user["name"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"status": "otp_sent", "email": str(payload.email).lower()}


@router.post("/login")
async def login(payload: LoginRequest) -> dict:
    _require_graph()
    if not settings.jwt_configured:
        raise HTTPException(500, "JWT_SECRET is missing or too short. Set it in backend/.env")
    user = await users.get_by_email(str(payload.email))
    if user is None or not security.verify_password(payload.password, user.get("password_hash") or ""):
        raise HTTPException(401, "Invalid email or password")
    if not user.get("verified"):
        raise HTTPException(403, "Verify the OTP sent to your email before signing in")
    return _token_payload(user)


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"user": user}
