-- ─────────────────────────────────────────────────────────────────────────
-- Distributed AI Document Intelligence — schema
--
-- A document moves through an explicit lifecycle instead of just "exists":
--   QUEUED       -> created by the gateway the moment the file lands
--   PROCESSING   -> a worker has claimed it from the stream
--   COMPLETED    -> extracted + passed all validators with high confidence
--   NEEDS_REVIEW -> extracted, but low confidence or a validator tripped
--   FAILED       -> exhausted all retries (also pushed to the DLQ stream)
-- ─────────────────────────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE doc_status AS ENUM (
        'QUEUED', 'PROCESSING', 'COMPLETED', 'NEEDS_REVIEW', 'FAILED'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS documents (
    task_id            UUID PRIMARY KEY,
    filename           VARCHAR(255) NOT NULL,
    status             doc_status   NOT NULL DEFAULT 'QUEUED',
    attempts           INT          NOT NULL DEFAULT 0,

    -- Normalized, queryable extraction (nullable until processed)
    vendor_name        VARCHAR(255),
    total_amount       NUMERIC(12, 2),
    doc_date           DATE,

    -- Raw model payload 
    structured_data    JSONB,            
    confidence         JSONB,            
    overall_confidence NUMERIC(4, 3),    
    review_reasons     TEXT[],           

    error_message      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS line_items (
    id          SERIAL PRIMARY KEY,
    task_id     UUID NOT NULL REFERENCES documents(task_id) ON DELETE CASCADE,
    description VARCHAR(255),
    amount      NUMERIC(12, 2)
);

CREATE INDEX IF NOT EXISTS idx_documents_status     ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_vendor     ON documents (vendor_name);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents (created_at);
CREATE INDEX IF NOT EXISTS idx_line_items_task      ON line_items (task_id);