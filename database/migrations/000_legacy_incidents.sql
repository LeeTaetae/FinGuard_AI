CREATE TABLE incidents_migrated (
    incident_id TEXT PRIMARY KEY,
    request_id TEXT UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    risk_level TEXT,
    consultation_id TEXT REFERENCES consultations(consultation_id),
    transaction_id TEXT REFERENCES transactions(transaction_id),
    status TEXT DEFAULT 'RECEIVED',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    risk_score INTEGER,
    rpa_status TEXT DEFAULT 'NOT_STARTED'
);

INSERT INTO incidents_migrated (
    incident_id, customer_id, category, summary, risk_level, consultation_id,
    transaction_id, status, created_at, risk_score, rpa_status
)
SELECT i.incident_id, c.customer_id, COALESCE(NULLIF(c.category, ''), 'OTHER'),
       COALESCE(NULLIF(c.ai_summary, ''), NULLIF(c.conversation, ''), '이전 사고 접수'),
       i.risk_level, i.consultation_id, i.transaction_id, i.status,
       i.created_at, i.risk_score, i.rpa_status
FROM incidents i LEFT JOIN consultations c ON c.consultation_id = i.consultation_id;

DROP TABLE incidents;
ALTER TABLE incidents_migrated RENAME TO incidents;
