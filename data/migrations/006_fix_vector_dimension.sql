-- Migration 006: Fix vector dimension from 1536 to 1024
-- Titan Text Embeddings V2 returns 1024-dimensional vectors

-- Drop the old vector index
DROP INDEX IF EXISTS idx_incident_embeddings_vec;

-- Drop existing embeddings (they were fake/wrong dimension anyway)
TRUNCATE incident_embeddings;

-- Alter the column type
ALTER TABLE incident_embeddings ALTER COLUMN embedding SET DATA TYPE VECTOR(1024);

-- Recreate the vector index with correct CockroachDB syntax
CREATE INDEX idx_incident_embeddings_vec
    ON incident_embeddings USING hnsw (embedding vector_cosine_ops);
