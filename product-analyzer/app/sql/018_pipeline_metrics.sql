-- Métricas operativas del ciclo de ingesta.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    duration_ms INTEGER,
    expired_products INTEGER NOT NULL DEFAULT 0,
    active_products INTEGER NOT NULL DEFAULT 0,
    high_opportunity INTEGER NOT NULL DEFAULT 0,
    amazon_products INTEGER NOT NULL DEFAULT 0,
    mercadolibre_products INTEGER NOT NULL DEFAULT 0,
    tiktok_products INTEGER NOT NULL DEFAULT 0,
    telegram_prepared INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at DESC);
