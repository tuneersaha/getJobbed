"""
Tests for app/auth.py — Google ID token validation + get_or_create_user.

Per testing-with-discernment:
- Token validation: unit tests (mock JWKS — no real network call).
- get_or_create_user: integration tests (real DB — tests the SQL, not a mock).
- We test: valid token accepted, expired rejected, wrong iss rejected,
  wrong aud rejected, unknown sub rejected (no DB row), idempotency.

TDD: tests written BEFORE app/auth.py. Will fail with ImportError until implemented.
"""

import time
import uuid
import pytest
import psycopg
from unittest.mock import patch, MagicMock

# ─── Unit: Token validation ───────────────────────────────────────────────────

class TestDecodeGoogleToken:
    """Unit tests for decode_google_token — mocked JWKS, no network."""

    def _make_payload(self, **overrides):
        now = int(time.time())
        return {
            "iss": "https://accounts.google.com",
            "aud": "test-client-id",
            "sub": "google-sub-123",
            "email": "user@example.com",
            "exp": now + 3600,
            "iat": now,
            **overrides,
        }

    def test_valid_token_returns_claims(self, monkeypatch):
        """Valid token with correct iss/aud/exp returns decoded claims."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

        payload = self._make_payload()

        with patch("app.auth._fetch_google_public_keys", return_value={"kid1": "fake-key"}):
            with patch("app.auth._decode_with_jose", return_value=payload):
                from app.auth import decode_google_token
                claims = decode_google_token("fake.token.here")

        assert claims["sub"] == "google-sub-123"
        assert claims["email"] == "user@example.com"

    def test_expired_token_raises(self, monkeypatch):
        """Token with exp in the past raises an AuthError."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

        from app.auth import AuthError
        from jose import ExpiredSignatureError

        with patch("app.auth._fetch_google_public_keys", return_value={}):
            with patch("app.auth._decode_with_jose", side_effect=ExpiredSignatureError("expired")):
                with pytest.raises(AuthError, match="expired"):
                    from app.auth import decode_google_token
                    decode_google_token("expired.token.here")

    def test_wrong_issuer_raises(self, monkeypatch):
        """Token from unexpected issuer raises AuthError."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

        payload = self._make_payload(iss="https://evil.com")

        from app.auth import AuthError
        with patch("app.auth._fetch_google_public_keys", return_value={"k": "v"}):
            with patch("app.auth._decode_with_jose", return_value=payload):
                with pytest.raises(AuthError, match="issuer"):
                    from app.auth import decode_google_token
                    decode_google_token("bad.issuer.token")

    def test_wrong_audience_raises(self, monkeypatch):
        """Token for a different audience raises AuthError."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

        payload = self._make_payload(aud="other-client-id")

        from app.auth import AuthError
        with patch("app.auth._fetch_google_public_keys", return_value={"k": "v"}):
            with patch("app.auth._decode_with_jose", return_value=payload):
                with pytest.raises(AuthError, match="audience"):
                    from app.auth import decode_google_token
                    decode_google_token("wrong.aud.token")

    def test_missing_sub_raises(self, monkeypatch):
        """Token without sub claim raises AuthError."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")

        payload = self._make_payload()
        del payload["sub"]

        from app.auth import AuthError
        with patch("app.auth._fetch_google_public_keys", return_value={"k": "v"}):
            with patch("app.auth._decode_with_jose", return_value=payload):
                with pytest.raises(AuthError, match="sub"):
                    from app.auth import decode_google_token
                    decode_google_token("no.sub.token")


# ─── Integration: get_or_create_user ─────────────────────────────────────────

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
class TestGetOrCreateUser:
    """Integration tests — real Postgres, transaction rolled back after each test."""

    @pytest.mark.asyncio
    async def test_creates_user_on_first_call(self, async_db_conn):
        """First call with a new google_sub inserts a users row and returns UUID."""
        from app.auth import get_or_create_user

        google_sub = f"sub-{uuid.uuid4()}"
        email = f"{uuid.uuid4()}@example.com"

        user_id = await get_or_create_user(google_sub, email, conn=async_db_conn)

        assert user_id is not None
        # Verify row exists in DB
        row = await async_db_conn.fetchrow(
            "SELECT id, google_sub, email FROM users WHERE id = $1",
            user_id,
        )
        assert row is not None
        assert row["google_sub"] == google_sub
        assert row["email"] == email

    @pytest.mark.asyncio
    async def test_returns_same_id_on_second_call(self, async_db_conn):
        """Second call with same google_sub returns the same user_id (idempotent)."""
        from app.auth import get_or_create_user

        google_sub = f"sub-{uuid.uuid4()}"
        email = f"{uuid.uuid4()}@example.com"

        id1 = await get_or_create_user(google_sub, email, conn=async_db_conn)
        id2 = await get_or_create_user(google_sub, email, conn=async_db_conn)

        assert id1 == id2

    @pytest.mark.asyncio
    async def test_different_subs_get_different_ids(self, async_db_conn):
        """Two different google_subs get two distinct user_ids."""
        from app.auth import get_or_create_user

        id1 = await get_or_create_user(f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@example.com", conn=async_db_conn)
        id2 = await get_or_create_user(f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@example.com", conn=async_db_conn)

        assert id1 != id2
