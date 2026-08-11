"""Populate Lakebase pgvector tables for the current activities."""

import os
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import psycopg2
from sentence_transformers import SentenceTransformer

MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DB_URL = os.environ["LAKEBASE_URL"]

parsed = urlparse(DB_URL)
conn = psycopg2.connect(
    host=parsed.hostname,
    port=parsed.port or 5432,
    dbname=parsed.path.lstrip("/") or "databricks_postgres",
    user=unquote(parsed.username or ""),
    password=parsed.password,
    sslmode="require",
)

with conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.activity_id, a.name, d.place_name, a.category,
                   a.outdoor, a.requires_good_weather, a.duration_minutes,
                   a.notes
            FROM activities a
            LEFT JOIN destinations d ON d.destination_id = a.destination_id
            ORDER BY a.activity_id
        """)
        activities = cur.fetchall()

    model = SentenceTransformer(MODEL, cache_folder="/tmp/.cache/huggingface")
    documents = []
    for activity_id, name, place, category, outdoor, good_weather, duration, notes in activities:
        text = (
            f"Activity: {name}. Destination: {place or ''}. Category: {category or ''}. "
            f"Outdoor: {outdoor}. Requires good weather: {good_weather}. "
            f"Duration: {duration or ''} minutes. Notes: {notes or ''}"
        )
        documents.append((activity_id, name, place, text))

    vectors = model.encode([row[3] for row in documents], normalize_embeddings=True)
    with conn.cursor() as cur:
        for (activity_id, name, place, text), vector in zip(documents, vectors):
            cur.execute("""
                INSERT INTO activity_documents
                    (activity_id, activity_name, destination_name, document_text, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (activity_id) DO UPDATE SET
                    activity_name = EXCLUDED.activity_name,
                    destination_name = EXCLUDED.destination_name,
                    document_text = EXCLUDED.document_text,
                    updated_at = now()
            """, (str(activity_id), name, place, text))
            cur.execute("""
                INSERT INTO activity_embeddings
                    (activity_id, chunk_index, chunk_text, embedding, model_name, created_at)
                VALUES (%s, 0, %s, %s::vector, %s, %s)
                ON CONFLICT (activity_id, chunk_index) DO UPDATE SET
                    chunk_text = EXCLUDED.chunk_text,
                    embedding = EXCLUDED.embedding,
                    model_name = EXCLUDED.model_name,
                    created_at = EXCLUDED.created_at
            """, (
                str(activity_id), text, vector.tolist(), MODEL,
                datetime.now(timezone.utc),
            ))

print(f"Embedded {len(documents)} activities using {MODEL}")
