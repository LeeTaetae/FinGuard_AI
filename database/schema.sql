
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
    request_id TEXT UNIQUE,              -- UiPath 중복 접수 방지 (수동 접수는 NULL)
    customer_id TEXT NOT NULL,
    category TEXT NOT NULL,              -- ai.consultation_analyzer.Category 값
    summary TEXT NOT NULL,
    risk_level TEXT,                     -- LOW / MEDIUM / HIGH
    consultation_id TEXT,
    transaction_id TEXT,
    status TEXT DEFAULT 'RECEIVED',      -- RECEIVED / IN_PROGRESS / DONE
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id),
    FOREIGN KEY (consultation_id)
        REFERENCES consultations(consultation_id),
    FOREIGN KEY (transaction_id)
        REFERENCES transactions(transaction_id)
);
