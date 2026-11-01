CREATE TABLE IF NOT EXISTS aliexpress_oauth_tokens (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    access_token_ciphertext TEXT NOT NULL,
    refresh_token_ciphertext TEXT,
    access_expires_at TIMESTAMPTZ,
    refresh_expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aliexpress_oauth_states (
    state_hash CHAR(64) PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aliexpress_oauth_states_expiry
    ON aliexpress_oauth_states(expires_at);
