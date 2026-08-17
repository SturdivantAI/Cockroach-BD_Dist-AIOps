-- Migration 002: Create incident_embeddings table with Distributed Vector Indexing
-- This is the core CockroachDB hackathon feature: native VECTOR column + cosine index

CREATE TABLE IF NOT EXISTS incident_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    embedding VECTOR(1536) NOT NULL,  -- Titan Embeddings v2 dimension
    chunk_text TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::JSONB,
    embedding_type STRING NOT NULL DEFAULT 'incident' CHECK (
        embedding_type IN ('incident', 'resolution', 'symptom', 'root_cause')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    INDEX idx_embeddings_incident (incident_id),
    INDEX idx_embeddings_type (embedding_type)
);

-- Distributed Vector Index for cosine similarity search
-- This is CockroachDB's native vector indexing — no separate vector store needed
--CREATE INDEX idx_incident_embeddings_vec
    --ON incident_embeddings USING vector_cosine(embedding);
