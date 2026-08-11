"""Run in a Databricks notebook to build Lakebase activity embeddings.

Uses the same successful pattern as ingest_weather_embeddings: MiniLM 384-D
embeddings, chunk-level deduplication, and cosine similarity in pgvector.
"""

# Databricks notebook setup:
# %pip install -q 'databricks-sdk>=0.118.0' sentence-transformers pandas psycopg2-binary
# dbutils.library.restartPython()

import os
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import psycopg2
from databricks.sdk import WorkspaceClient
from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 32
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

w = WorkspaceClient()
lakebase_url = w.dbutils.secrets.get_secret(
    # Use the same Lakebase secret as the app by default. Override only when
    # the app is intentionally configured to use a separate vector database.
    scope=os.getenv("LAKEBASE_SECRET_SCOPE", "database"),
    key=os.getenv("LAKEBASE_SECRET_KEY", "lakebase-url"),
)
parsed = urlparse(lakebase_url)
conn_args = dict(
    host=parsed.hostname,
    port=parsed.port or 5432,
    dbname=parsed.path.lstrip("/"),
    user=parsed.username,
    password=parsed.password,
    sslmode="require",
)

def chunks(text):
    if not text or len(text) <= CHUNK_SIZE:
        return [(text or "", 0)]
    step = CHUNK_SIZE - CHUNK_OVERLAP
    return [(text[i:i + CHUNK_SIZE], n) for n, i in enumerate(range(0, len(text), step))]

with psycopg2.connect(**conn_args) as conn:
    docs = pd.read_sql_query("""
        SELECT a.activity_id, a.name, d.place_name,
               COALESCE(a.name, '') || ' at ' || COALESCE(d.place_name, '') ||
               ' outdoor=' || a.outdoor::text ||
               ' requires_good_weather=' || a.requires_good_weather::text ||
               ' duration_minutes=' || a.duration_minutes::text AS document_text
        FROM activities a
        LEFT JOIN destinations d ON d.destination_id = a.destination_id
    """, conn)

    pending = pd.read_sql_query("""
        SELECT d.* FROM ({}) d
        WHERE NOT EXISTS (
            SELECT 1 FROM activity_embeddings e
            WHERE e.activity_id = d.activity_id
        )
    """.format("""
        SELECT a.activity_id, a.name, d.place_name,
               COALESCE(a.name, '') || ' at ' || COALESCE(d.place_name, '') AS document_text
        FROM activities a LEFT JOIN destinations d ON d.destination_id = a.destination_id
    """), conn)

    model = SentenceTransformer(MODEL_NAME, cache_folder="/tmp/.cache/huggingface")
    rows = []
    for _, doc in pending.iterrows():
        for text, index in chunks(doc.document_text):
            rows.append((str(doc.activity_id), doc.name, doc.place_name, doc.document_text, index, text))

    if rows:
        vectors = model.encode([row[5] for row in rows], batch_size=BATCH_SIZE, show_progress_bar=True)
        with conn.cursor() as cur:
            for row, vector in zip(rows, vectors):
                cur.execute("""
                    INSERT INTO activity_documents
                        (activity_id, activity_name, destination_name, document_text)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (activity_id) DO UPDATE SET
                        activity_name = EXCLUDED.activity_name,
                        destination_name = EXCLUDED.destination_name,
                        document_text = EXCLUDED.document_text,
                        updated_at = now()
                """, row[:4])
                cur.execute("""
                    INSERT INTO activity_embeddings
                        (activity_id, chunk_index, chunk_text, embedding, model_name, created_at)
                    VALUES (%s, %s, %s, %s::vector, %s, %s)
                    ON CONFLICT (activity_id, chunk_index) DO NOTHING
                """, (row[0], row[4], row[5], vector.tolist(), MODEL_NAME, datetime.utcnow()))
        conn.commit()

print(f"Embedded {len(rows)} activity chunks using {MODEL_NAME}")
