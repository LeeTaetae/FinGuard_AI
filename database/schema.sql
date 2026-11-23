
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    card_last4 TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    country TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    status TEXT DEFAULT 'COMPLETED',
    FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS consultations (
    consultation_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    conversation TEXT,
    ai_summary TEXT,
    category TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    consultation_id TEXT NOT NULL,
    transaction_id TEXT,
    risk_score INTEGER,
    risk_level TEXT,
    status TEXT DEFAULT 'PENDING',
    rpa_status TEXT DEFAULT 'NOT_STARTED',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (consultation_id)
        REFERENCES consultations(consultation_id),
    FOREIGN KEY (transaction_id)
        REFERENCES transactions(transaction_id)
);
