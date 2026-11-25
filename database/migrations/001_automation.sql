CREATE TABLE IF NOT EXISTS automation_requests (
    request_id TEXT NOT NULL PRIMARY KEY,
    created_by TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    consultation_id TEXT NOT NULL UNIQUE REFERENCES consultations(consultation_id),
    transaction_id TEXT REFERENCES transactions(transaction_id),
    incident_id TEXT UNIQUE REFERENCES incidents(incident_id),
    ai_result_json TEXT,
    fds_result_json TEXT NOT NULL,
    review_required INTEGER NOT NULL CHECK (review_required IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (
        status IN ('PENDING_APPROVAL', 'APPROVED', 'QUEUED', 'RUNNING',
                   'SUCCEEDED', 'FAILED', 'REJECTED')
    ),
    decision_by TEXT,
    decision_at TEXT,
    incident_category TEXT,
    incident_summary TEXT,
    incident_risk_level TEXT,
    dispatch_pending INTEGER NOT NULL DEFAULT 0 CHECK (
        dispatch_pending IN (0, 1) AND (dispatch_pending = 0 OR status = 'APPROVED')
    ),
    external_execution_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (created_by, idempotency_key)
);

CREATE TABLE IF NOT EXISTS automation_events (
    event_id INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES automation_requests(request_id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS automation_events_request_idx
    ON automation_events (request_id, event_id);
