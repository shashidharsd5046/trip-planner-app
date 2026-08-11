-- Parameter order: %(embedding)s, %(limit)s
SELECT
    ad.activity_id,
    a.name,
    a.outdoor,
    a.requires_good_weather,
    a.duration_minutes,
    d.place_name AS destination,
    1 - (ae.embedding <=> %(embedding)s::vector) AS similarity
FROM activity_embeddings ae
JOIN activity_documents ad ON ad.activity_id = ae.activity_id
JOIN activities a ON a.activity_id = ae.activity_id
LEFT JOIN destinations d ON d.destination_id = a.destination_id
ORDER BY ae.embedding <=> %(embedding)s::vector
LIMIT %(limit)s;
