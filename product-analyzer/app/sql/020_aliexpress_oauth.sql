CREATE TABLE IF NOT EXISTS aliexpress_oauth_state (
    state_hash CHAR(64) PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aliexpress_oauth_tokens (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    user_id TEXT,
    seller_id TEXT,
    account TEXT,
    account_platform TEXT,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    expires_at TIMESTAMPTZ,
    refresh_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
