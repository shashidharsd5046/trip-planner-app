"""Synchronous activity embedding used after destination ingestion."""

import os
from datetime import datetime, timezone

from sentence_transformers import SentenceTransformer

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(
            os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            cache_folder="/tmp/.cache/huggingface",
        )
    return _model


def embed_destination_activities(conn, destination_id: str) -> int:
    """Upsert documents and 384-D vectors for all activities at a destination."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.activity_id, a.name, d.place_name, a.category,
                   a.outdoor, a.requires_good_weather, a.duration_minutes, a.notes
            FROM activities a
            JOIN destinations d ON d.destination_id = a.destination_id
            WHERE a.destination_id = %s
        """, (destination_id,))
        rows = cur.fetchall()

    if not rows:
        return 0

    documents = []
    for activity_id, name, place, category, outdoor, good_weather, duration, notes in rows:
        text = (
            f"Activity: {name}. Destination: {place or ''}. Category: {category or ''}. "
            f"Outdoor: {outdoor}. Requires good weather: {good_weather}. "
            f"Duration: {duration or ''} minutes. Notes: {notes or ''}"
        )
        documents.append((activity_id, name, place, text))

    vectors = _get_model().encode([row[3] for row in documents], normalize_embeddings=True)
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
                str(activity_id), text, vector.tolist(),
                os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
                datetime.now(timezone.utc),
            ))
    conn.commit()
    return len(documents)
