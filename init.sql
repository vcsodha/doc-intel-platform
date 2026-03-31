CREATE TABLE IF NOT EXISTS processed_documents (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    structured_data JSONB NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
