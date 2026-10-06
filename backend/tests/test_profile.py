"""
Tests for profile/settings endpoints.

GET /api/profile
PUT /api/profile

Unit: ProfileUpdate validation constraints.
Integration: real DB reads/writes via async_db_conn.
"""

import uuid
import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _insert_user(conn):
    cur = await conn.execute(
        "INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
        [f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@test.com"],
    )
    return str((await cur.fetchone())[0])


# ─── Unit: ProfileUpdate validation ──────────────────────────────────────────

class TestProfileUpdateValidation:
    def test_experience_years_max_10(self):
        from pydantic import ValidationError
        from app.schemas import ProfileUpdate
        with pytest.raises(ValidationError):
            ProfileUpdate(experience_years=11)

    def test_experience_years_min_0(self):
        from pydantic import ValidationError
        from app.schemas import ProfileUpdate
        with pytest.raises(ValidationError):
            ProfileUpdate(experience_years=-1)

    def test_tailor_threshold_max_1(self):
        from pydantic import ValidationError
        from app.schemas import ProfileUpdate
        with pytest.raises(ValidationError):
            ProfileUpdate(tailor_threshold=1.1)

    def test_tailor_threshold_min_0(self):
        from pydantic import ValidationError
        from app.schemas import ProfileUpdate
        with pytest.raises(ValidationError):
            ProfileUpdate(tailor_threshold=-0.1)

    def test_experience_level_must_be_valid(self):
        from pydantic import ValidationError
        from app.schemas import ProfileUpdate
        with pytest.raises(ValidationError):
            ProfileUpdate(experience_level="expert")

    def test_valid_profile_update(self):
        from app.schemas import ProfileUpdate
        p = ProfileUpdate(
            desired_roles=["Data Engineer"],
            experience_years=1,
            tailor_threshold=0.5,
        )
        assert p.desired_roles == ["Data Engineer"]
        assert p.experience_years == 1

    def test_partial_update_none_fields_excluded(self):
        from app.schemas import ProfileUpdate
        p = ProfileUpdate(experience_years=2)
        excluded = p.model_dump(exclude_none=True)
        assert "experience_years" in excluded
        assert "desired_roles" not in excluded


# ─── Integration: GET /api/profile ───────────────────────────────────────────

@pytest.mark.integration
class TestGetProfile:
    @pytest.mark.asyncio
    async def test_default_profile_values(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)

        # Auto-create profile
        await async_db_conn.execute(
            "INSERT INTO user_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            [user_id],
        )

        cur = await async_db_conn.execute(
            """
            SELECT desired_roles, experience_years, remote_ok, tailor_threshold
            FROM user_profiles WHERE user_id = %s
            """,
            [user_id],
        )
        row = await cur.fetchone()
        assert row[0] == []       # desired_roles default '{}'
        assert row[1] == 1        # experience_years default 1
        assert row[2] is True     # remote_ok default TRUE
        assert float(row[3]) == 0.40  # tailor_threshold default 0.40

    @pytest.mark.asyncio
    async def test_unique_profile_per_user(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)

        # Insert twice — ON CONFLICT DO NOTHING
        for _ in range(2):
            await async_db_conn.execute(
                "INSERT INTO user_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                [user_id],
            )

        cur = await async_db_conn.execute(
            "SELECT COUNT(*) FROM user_profiles WHERE user_id = %s",
            [user_id],
        )
        row = await cur.fetchone()
        assert row[0] == 1


# ─── Integration: PUT /api/profile ───────────────────────────────────────────

@pytest.mark.integration
class TestUpdateProfile:
    @pytest.mark.asyncio
    async def test_update_desired_roles(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        await async_db_conn.execute(
            "INSERT INTO user_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            [user_id],
        )

        await async_db_conn.execute(
            "UPDATE user_profiles SET desired_roles = %s, updated_at = NOW() WHERE user_id = %s",
            [["Data Engineer", "Backend SWE"], user_id],
        )

        cur = await async_db_conn.execute(
            "SELECT desired_roles FROM user_profiles WHERE user_id = %s",
            [user_id],
        )
        row = await cur.fetchone()
        assert "Data Engineer" in row[0]
        assert "Backend SWE" in row[0]

    @pytest.mark.asyncio
    async def test_update_tailor_threshold(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        await async_db_conn.execute(
            "INSERT INTO user_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            [user_id],
        )

        await async_db_conn.execute(
            "UPDATE user_profiles SET tailor_threshold = %s, updated_at = NOW() WHERE user_id = %s",
            [0.60, user_id],
        )

        cur = await async_db_conn.execute(
            "SELECT tailor_threshold FROM user_profiles WHERE user_id = %s",
            [user_id],
        )
        row = await cur.fetchone()
        assert float(row[0]) == 0.60

    @pytest.mark.asyncio
    async def test_tailor_threshold_db_constraint(self, async_db_conn):
        """DB enforces tailor_threshold BETWEEN 0.0 AND 1.0."""
        import psycopg
        user_id = await _insert_user(async_db_conn)
        await async_db_conn.execute(
            "INSERT INTO user_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            [user_id],
        )

        with pytest.raises(Exception):
            await async_db_conn.execute(
                "UPDATE user_profiles SET tailor_threshold = 1.5 WHERE user_id = %s",
                [user_id],
            )
            await async_db_conn.rollback()
