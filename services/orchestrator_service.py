"""Durable one-shot dispatch and reconciliation for approved CRM requests."""

from uuid import uuid4

from rpa.orchestrator_client import OrchestratorError
from services.automation_service import AutomationConflict, Status, get_request
from services.incident_service import create_robot_incident


def execution(conn, request_id):
    row = conn.execute(
        "SELECT * FROM orchestrator_executions WHERE request_id = ?", (request_id,)
    ).fetchone()
    return dict(row) if row else None


def confirmed_receipt(conn, current):
    return conn.execute(
        """SELECT incident_id FROM incidents WHERE request_id = ?
           AND customer_id = ? AND consultation_id = ?""",
        (current["request_id"], current["customer_id"], current["consultation_id"]),
    ).fetchone()


def _record(conn, request_id, actor, *, status, job_state, key=None,
            result=None, incident=None, error=None):
    previous = get_request(conn, request_id)
    conn.execute(
        """UPDATE orchestrator_executions
           SET job_key = ?, job_state = ?, result_status = ?, error_message = ?
           WHERE request_id = ?""",
        (key, job_state, result, error, request_id),
    )
    conn.execute(
        """UPDATE automation_requests SET status = ?, dispatch_pending = 0,
           external_execution_id = ?, incident_id = ? WHERE request_id = ?""",
        (status, key, incident, request_id),
    )
    event = conn.execute(
        """INSERT INTO automation_events (request_id, from_status, to_status, actor_id)
           VALUES (?, ?, ?, ?)""", (request_id, previous["status"], status, actor),
    )
    conn.execute(
        "INSERT INTO orchestrator_execution_events VALUES (?, ?, ?, ?, ?, ?)",
        (event.lastrowid, key, job_state, result, incident, error),
    )


def dispatch(conn, request_id, actor, client, *, guard=None):
    current = get_request(conn, request_id)
    if current["status"] != Status.APPROVED or not current["dispatch_pending"]:
        raise AutomationConflict("실행 대기 중인 승인 요청만 시작할 수 있습니다.")
    release = client.release_key()
    dispatch_id = uuid4().hex
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if guard:
            guard(conn)
        current = get_request(conn, request_id)
        if (current["status"] != Status.APPROVED or not current["dispatch_pending"]
                or execution(conn, request_id)):
            raise AutomationConflict("실행 대기 중인 승인 요청만 시작할 수 있습니다.")
        if not current["decision_at"] or not conn.execute(
            """SELECT 1 FROM automation_events WHERE request_id = ? AND actor_id = ?
               AND from_status = ? AND to_status = ?""",
            (request_id, current["decision_by"], Status.PENDING_APPROVAL, Status.APPROVED),
        ).fetchone():
            raise AutomationConflict("승인자와 승인 이력을 확인할 수 없습니다.")
        conn.execute(
            "INSERT INTO orchestrator_executions (request_id, dispatch_id) VALUES (?, ?)",
            (request_id, dispatch_id),
        )
        _record(conn, request_id, actor, status=Status.QUEUED, job_state="STARTING")

    try:
        job = client.start(release, request_id, dispatch_id)
    except OrchestratorError as exc:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if execution(conn, request_id)["job_state"] == "STARTING":
                _record(conn, request_id, actor, status=Status.QUEUED, job_state="UNKNOWN",
                        error=str(exc))
        raise

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        active = execution(conn, request_id)
        if not active["job_key"]:
            # Terminal responses are reconciled by refresh against the CRM receipt.
            _record(conn, request_id, actor, status=Status.QUEUED, key=job.key,
                    job_state=job.state)
        elif active["job_key"] != job.key:
            raise AutomationConflict("복구한 Job과 실행 응답의 Job이 일치하지 않습니다.")


def refresh(conn, request_id, actor, client):
    current = get_request(conn, request_id)
    active = execution(conn, request_id)
    if not active:
        raise AutomationConflict("조회할 Orchestrator 실행이 없습니다.")
    if current["status"] in (Status.SUCCEEDED, Status.FAILED):
        return
    key = active["job_key"] or client.recover(request_id, active["dispatch_id"])
    if not key:
        raise AutomationConflict("기존 Job이 아직 확인되지 않았습니다. 실행 상태를 다시 조회하세요.")
    job = client.retrieve(key)
    if job.key != key:
        raise OrchestratorError("조회한 Job 식별자가 일치하지 않습니다.")

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if execution(conn, request_id) != active:
            return
        current = get_request(conn, request_id)
        if current["status"] in (Status.SUCCEEDED, Status.FAILED):
            return
        status = Status.RUNNING if job.state == "Running" else current["status"]
        incident = result = error = None
        if job.state == "Successful":
            status = Status.FAILED
            result = job.outputs.get("out_Status")
            result = result if result in ("SUCCESS", "DUPLICATE", "FAILED") else None
            confirmed = confirmed_receipt(conn, current)
            if (result in ("SUCCESS", "DUPLICATE") and confirmed
                    and job.outputs.get("out_RequestId") == request_id
                    and job.outputs.get("out_IncidentId") == confirmed["incident_id"]):
                status, incident = Status.SUCCEEDED, confirmed["incident_id"]
            else:
                workflow_error = job.outputs.get("out_Error")
                error = (workflow_error if workflow_error in (
                    "ROBOT_CONFIG", "CRM_REJECTED", "CRM_TIMEOUT", "CRM_UNAVAILABLE",
                    "CRM_INVALID_RESPONSE",
                    "ROBOT_REQUEST_ID_MISSING", "ROBOT_DISPATCH_ID_MISSING",
                    "ROBOT_TOKEN_MISSING", "ROBOT_CRM_URL_INVALID",
                ) else "Job은 완료됐지만 CRM 접수 결과를 확인할 수 없습니다.")
        elif job.state in ("Faulted", "Stopped", "Canceled", "Cancelled", "Failed"):
            status, error = Status.FAILED, "Orchestrator Job이 실패하거나 중단되었습니다."
        if (key, job.state, result, error, status) != (
                active["job_key"], active["job_state"], active["result_status"],
                active["error_message"], current["status"]):
            _record(conn, request_id, actor, status=status, key=key, job_state=job.state,
                    result=result, incident=incident, error=error)


def submit_receipt(conn, request_id, dispatch_id, categories):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        active = execution(conn, request_id)
        if not active or active["dispatch_id"] != dispatch_id:
            raise AutomationConflict("요청과 실행 식별자를 확인할 수 없습니다.")
        current = get_request(conn, request_id)
        eligible = current["status"] in (Status.QUEUED, Status.RUNNING)
        incident_id, duplicate = create_robot_incident(
            conn, current, categories=categories, allow_insert=eligible,
        )
        if not duplicate:
            event = conn.execute(
                """INSERT INTO automation_events (request_id, from_status, to_status, actor_id)
                   VALUES (?, ?, ?, 'robot:FinGuard-Robot')""",
                (request_id, current["status"], current["status"]),
            )
            conn.execute(
                "INSERT INTO orchestrator_execution_events VALUES (?, ?, ?, ?, ?, ?)",
                (event.lastrowid, active["job_key"], active["job_state"], "RECEIVED", incident_id, None),
            )
        return incident_id, duplicate
