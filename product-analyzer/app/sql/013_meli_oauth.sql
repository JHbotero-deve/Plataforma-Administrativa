CREATE TABLE IF NOT EXISTS meli_oauth_state (
    state VARCHAR(128) PRIMARY KEY,
    code_verifier VARCHAR(256) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS meli_oauth_tokens (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    user_id BIGINT,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    scope TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);