"""
Google OAuth2 authentication for FastAPI.

FastAPI validates Google ID tokens only — no custom login endpoint.

Flow:
  next-auth (frontend) → Google → ID token
  Frontend: Authorization: Bearer <id_token>
  FastAPI: validate via JWKS → extract sub + email → get_or_create_user()

JWKS URL: https://www.googleapis.com/oauth2/v3/certs
Valid issuers: accounts.google.com or https://accounts.google.com

os.environ["GOOGLE_CLIENT_ID"] — KeyError at startup if missing (fail fast).
"""

import os
import logging
from functools import lru_cache

import httpx
from jose import jwt, JWTError, ExpiredSignatureError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

bearer_scheme = HTTPBearer()


class AuthError(Exception):
    """Raised when token validation fails — converted to 401 by require_auth."""
    pass


# ─── JWKS helpers (separated for test-mocking) ───────────────────────────────

def _fetch_google_public_keys() -> dict:
    """Fetch JWKS from Google. Returns {kid: public_key_pem}."""
    resp = httpx.get(_GOOGLE_JWKS_URL, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def _decode_with_jose(token: str, keys: dict, audience: str) -> dict:
    """
    Try decoding the token against each key in the JWKS.
    Returns the first successful decode. Raises JWTError if all keys fail.
    """
    last_error: Exception | None = None
    key_list = keys.get("keys", list(keys.values()))

    for key in key_list:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=audience,
                options={"verify_at_hash": False},
            )
        except ExpiredSignatureError:
            raise
        except JWTError as exc:
            last_error = exc

    raise last_error or JWTError("No matching key found")


# ─── Public API ──────────────────────────────────────────────────────────────

def decode_google_token(token: str) -> dict:
    """
    Validate a Google ID token. Returns the decoded claims dict.

    Raises AuthError for:
    - Expired token
    - Wrong issuer
    - Wrong audience (not our GOOGLE_CLIENT_ID)
    - Missing sub claim
    - Invalid signature
    """
    audience = os.environ["GOOGLE_CLIENT_ID"]

    try:
        keys = _fetch_google_public_keys()
        claims = _decode_with_jose(token, keys, audience)
    except ExpiredSignatureError:
        raise AuthError("Token expired")
    except JWTError as exc:
        raise AuthError(f"Invalid token: {exc}")

    # Validate issuer
    if claims.get("iss") not in _VALID_ISSUERS:
        raise AuthError(f"Invalid issuer: {claims.get('iss')!r}")

    # Validate audience
    token_aud = claims.get("aud")
    if isinstance(token_aud, list):
        if audience not in token_aud:
            raise AuthError("Token audience does not include our client_id")
    elif token_aud != audience:
        raise AuthError("Token audience does not match our client_id")

    # sub is required — it's our stable Google identity
    if not claims.get("sub"):
        raise AuthError("Token missing sub claim")

    return claims


async def get_or_create_user(
    google_sub: str,
    email: str,
    *,
    conn,
) -> str:
    """
    Upsert a users row for this Google sub and return the internal user_id.

    Safe to call multiple times with the same google_sub — the UNIQUE
    constraint on google_sub makes this idempotent.
    """
    cur = await conn.execute(
        "SELECT id FROM users WHERE google_sub = %s",
        [google_sub],
    )
    row = await cur.fetchone()
    if row:
        return str(row[0])

    cur = await conn.execute(
        """
        INSERT INTO users (google_sub, email)
        VALUES (%s, %s)
        ON CONFLICT (google_sub) DO UPDATE SET email = EXCLUDED.email
        RETURNING id
        """,
        [google_sub, email],
    )
    row = await cur.fetchone()
    return str(row[0])


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """
    FastAPI dependency. Validates the Bearer token and returns the internal user_id.

    Usage:
        @router.get("/api/jobs")
        async def list_jobs(user_id: str = Depends(require_auth)):
            ...

    Returns the user_id UUID string from users.id (NOT the Google sub).
    Raises 401 if the token is missing, expired, or invalid.
    """
    from app.db import get_conn

    try:
        claims = decode_google_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": str(exc)},
            headers={"WWW-Authenticate": "Bearer"},
        )

    google_sub: str = claims["sub"]
    email: str = claims.get("email", "")

    async with get_conn() as conn:
        user_id = await get_or_create_user(google_sub, email, conn=conn)

    return user_id
