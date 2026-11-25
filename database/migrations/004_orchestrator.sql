CREATE TABLE IF NOT EXISTS orchestrator_executions (
    request_id TEXT PRIMARY KEY REFERENCES automation_requests(request_id),
    dispatch_id TEXT NOT NULL UNIQUE,
    job_key TEXT UNIQUE,
    job_state TEXT NOT NULL DEFAULT 'STARTING',
    result_status TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS orchestrator_execution_events (
    event_id INTEGER PRIMARY KEY REFERENCES automation_events(event_id),
    job_key TEXT,
    job_state TEXT NOT NULL,
    result_status TEXT,
    incident_id TEXT REFERENCES incidents(incident_id),
    error_message TEXT
);
