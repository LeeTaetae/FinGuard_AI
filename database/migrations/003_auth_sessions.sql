CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT NOT NULL PRIMARY KEY,
    username TEXT NOT NULL REFERENCES auth_users(username) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS auth_sessions_expiry_idx ON auth_sessions (expires_at);
