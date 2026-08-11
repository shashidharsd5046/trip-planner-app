-- Run in the Lakebase SQL editor.
-- Use the dimension produced by the embedding model selected for your job.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS activity_documents (
    activity_id UUID PRIMARY KEY,
    activity_name TEXT NOT NULL,
    destination_name TEXT,
    document_text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS activity_embeddings (
    activity_id UUID NOT NULL REFERENCES activity_documents(activity_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(384) NOT NULL,
    model_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (activity_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS activity_embeddings_hnsw
ON activity_embeddings USING hnsw (embedding vector_cosine_ops);
