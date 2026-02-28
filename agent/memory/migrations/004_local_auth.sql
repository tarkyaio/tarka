-- Local authentication support: username/password admin users
-- This migration adds a local_users table for invite-only admin accounts.

CREATE TABLE IF NOT EXISTS local_users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,  -- bcrypt hash
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT,  -- Email of admin who created this user (NULL for initial admin)
    last_login_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX idx_local_users_email ON local_users(email);
CREATE INDEX idx_local_users_username ON local_users(username);
CREATE INDEX idx_local_users_is_active ON local_users(is_active);

-- Note: Initial admin user will be created by application on startup
-- if local_users table is empty and ADMIN_INITIAL_USERNAME/PASSWORD are set.
