import base64
import hashlib
import hmac

import httpx
from fastapi import HTTPException, Request

from .config import Settings


def current_user(request: Request, settings: Settings) -> dict:
    if not settings.better_auth_url:
        raise HTTPException(status_code=503, detail="Sign-in is not configured")
    headers = {}
    if cookie := request.headers.get("cookie"):
        headers["cookie"] = cookie
    if authorization := request.headers.get("authorization"):
        headers["authorization"] = authorization
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"{settings.better_auth_url.rstrip('/')}/api/auth/get-session",
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Sign-in service is unavailable") from exc
    try:
        session = response.json()
    except ValueError:
        session = None
    if response.status_code != 200 or not session:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    user = session.get("user")
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user


def optional_current_user(request: Request, settings: Settings) -> dict | None:
    """Return the signed-in user without making authentication a prerequisite."""
    if not settings.better_auth_url:
        return None
    try:
        return current_user(request, settings)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


def personal_feed_token(user_id: str, settings: Settings) -> str:
    if not settings.better_auth_secret:
        raise HTTPException(status_code=503, detail="Personal feeds are not configured")
    digest = hmac.new(
        settings.better_auth_secret.encode(), user_id.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
