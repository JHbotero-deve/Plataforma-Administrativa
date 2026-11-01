CREATE TABLE IF NOT EXISTS admin_publication_drafts (
    publication_id INTEGER PRIMARY KEY
        REFERENCES published_cards(id) ON DELETE CASCADE,
    subtitle TEXT,
    footer VARCHAR(200) NOT NULL DEFAULT 'Disponible en Central',
    accent VARCHAR(20) NOT NULL DEFAULT '#b6f23a',
    is_published BOOLEAN NOT NULL DEFAULT TRUE,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
