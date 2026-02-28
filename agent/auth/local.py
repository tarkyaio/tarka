from __future__ import annotations

from typing import Optional

import bcrypt
import psycopg

from agent.auth.models import AuthUser, LocalUser


def hash_password(password: str) -> str:
    """
    Hash password with bcrypt (cost factor 12).

    Args:
        password: Plain text password

    Returns:
        Bcrypt hash string
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify password against bcrypt hash with constant-time comparison.

    Args:
        password: Plain text password
        password_hash: Bcrypt hash

    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        # Handle invalid hash format gracefully
        return False


def authenticate_local(conn: psycopg.Connection, username: str, password: str) -> Optional[AuthUser]:
    """
    Authenticate local user with username/password.

    Args:
        conn: PostgreSQL connection
        username: Username
        password: Plain text password

    Returns:
        AuthUser if authentication succeeds, None otherwise
    """
    with conn.cursor() as cur:
        # Fetch user from database
        cur.execute(
            """
            SELECT id, email, username, password_hash, name, is_active
            FROM local_users
            WHERE username = %s
            """,
            (username,),
        )
        row = cur.fetchone()
        if not row:
            return None

        user_id, email, db_username, password_hash, name, is_active = row

        # Check if user is active
        if not is_active:
            return None

        # Verify password with constant-time comparison
        if not verify_password(password, password_hash):
            return None

        # Update last_login_at
        cur.execute(
            """
            UPDATE local_users
            SET last_login_at = NOW()
            WHERE id = %s
            """,
            (user_id,),
        )
        conn.commit()

        # Return AuthUser with provider="local"
        return AuthUser(
            provider="local",
            email=email,
            name=name,
            username=db_username,
            picture=None,
        )


def create_local_user(
    conn: psycopg.Connection,
    email: str,
    username: str,
    password: str,
    name: Optional[str],
    created_by: str,
) -> LocalUser:
    """
    Create new local user (invite-only, no self-registration).

    Args:
        conn: PostgreSQL connection
        email: User email
        username: Username (must be unique)
        password: Plain text password (will be hashed)
        name: Display name (optional)
        created_by: Email of admin who created this user

    Returns:
        Created LocalUser

    Raises:
        psycopg.IntegrityError: If email or username already exists
    """
    password_hash = hash_password(password)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO local_users (email, username, password_hash, name, created_by, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            RETURNING id, email, username, password_hash, name, created_at, created_by, last_login_at, is_active
            """,
            (email, username, password_hash, name, created_by),
        )
        row = cur.fetchone()
        conn.commit()

        if not row:
            raise ValueError("Failed to create user")

        user_id, email, username, password_hash, name, created_at, created_by, last_login_at, is_active = row
        return LocalUser(
            id=user_id,
            email=email,
            username=username,
            password_hash=password_hash,
            name=name,
            created_at=created_at,
            created_by=created_by,
            last_login_at=last_login_at,
            is_active=is_active,
        )


def initialize_admin_user(conn: psycopg.Connection, username: str, password: str) -> None:
    """
    Create initial admin user if local_users table is empty.

    This is called on application startup to ensure there's always an admin account.

    Args:
        conn: PostgreSQL connection
        username: Admin username (from ADMIN_INITIAL_USERNAME env var)
        password: Admin password (from ADMIN_INITIAL_PASSWORD env var)
    """
    if not username or not password:
        # Skip if not configured
        return

    with conn.cursor() as cur:
        # Check if any users exist
        cur.execute("SELECT COUNT(*) FROM local_users")
        row = cur.fetchone()
        count = row[0] if row else 0

        if count > 0:
            # Users already exist, skip initialization
            return

        # Create initial admin user
        password_hash = hash_password(password)
        cur.execute(
            """
            INSERT INTO local_users (email, username, password_hash, name, created_by, is_active)
            VALUES (%s, %s, %s, %s, NULL, TRUE)
            ON CONFLICT (username) DO NOTHING
            """,
            (
                f"{username}@local",  # Default email for initial admin
                username,
                password_hash,
                "Initial Admin",
                # created_by is NULL for initial admin (bootstrapped)
            ),
        )
        conn.commit()


def get_local_user_by_username(conn: psycopg.Connection, username: str) -> Optional[LocalUser]:
    """
    Get local user by username.

    Args:
        conn: PostgreSQL connection
        username: Username

    Returns:
        LocalUser if found, None otherwise
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, email, username, password_hash, name, created_at, created_by, last_login_at, is_active
            FROM local_users
            WHERE username = %s
            """,
            (username,),
        )
        row = cur.fetchone()
        if not row:
            return None

        user_id, email, username, password_hash, name, created_at, created_by, last_login_at, is_active = row
        return LocalUser(
            id=user_id,
            email=email,
            username=username,
            password_hash=password_hash,
            name=name,
            created_at=created_at,
            created_by=created_by,
            last_login_at=last_login_at,
            is_active=is_active,
        )
