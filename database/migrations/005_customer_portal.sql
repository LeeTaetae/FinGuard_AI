CREATE TABLE IF NOT EXISTS customer_accounts (
    account_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL UNIQUE REFERENCES customers(customer_id),
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES customer_accounts(account_id),
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS customer_sessions_expiry_idx ON customer_sessions(expires_at);

CREATE TABLE IF NOT EXISTS customer_reports (
    report_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES customer_accounts(account_id),
    request_id TEXT NOT NULL UNIQUE REFERENCES automation_requests(request_id),
    kind TEXT NOT NULL,
    occurred_at TEXT,
    amount INTEGER CHECK (amount IS NULL OR amount >= 0),
    assigned_to TEXT REFERENCES auth_users(username),
    needs_information INTEGER NOT NULL DEFAULT 0 CHECK (needs_information IN (0, 1)),
    rejection_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS customer_reports_account_idx ON customer_reports(account_id, created_at);
CREATE INDEX IF NOT EXISTS customer_reports_assignee_idx ON customer_reports(assigned_to, created_at);

CREATE TABLE IF NOT EXISTS customer_report_events (
    event_id INTEGER PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES customer_reports(report_id),
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS customer_report_events_report_idx ON customer_report_events(report_id, event_id);

CREATE TABLE IF NOT EXISTS agent_telephone_requests (
    request_id TEXT PRIMARY KEY REFERENCES automation_requests(request_id)
);
