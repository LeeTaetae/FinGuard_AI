CREATE TABLE IF NOT EXISTS customer_advice (
    advice_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES customer_accounts(account_id),
    conversation TEXT NOT NULL,
    reply TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS customer_advice_account_idx
    ON customer_advice(account_id, created_at, advice_id);
