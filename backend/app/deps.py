from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError

from app.services import security, users

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "Sign in required")
    try:
        payload = security.decode_token(creds.credentials)
    except ExpiredSignatureError as exc:
        raise HTTPException(401, "Session expired. Sign in again.") from exc
    except InvalidTokenError as exc:
        raise HTTPException(401, "Invalid token") from exc
    user = await users.get_by_id(str(payload.get("sub") or ""))
    if user is None or not user.get("verified"):
        raise HTTPException(401, "Account is not active")
    return users.public_user(user)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin role required")
    return user
