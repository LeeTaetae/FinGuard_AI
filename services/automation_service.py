import json
from enum import Enum
from uuid import uuid4

from services.incident_service import validate_incident_fields


class Status(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


TRANSITIONS = {
    Status.PENDING_APPROVAL: {Status.APPROVED, Status.REJECTED},
    Status.APPROVED: {Status.QUEUED},
    Status.QUEUED: {Status.RUNNING, Status.FAILED},
    Status.RUNNING: {Status.SUCCEEDED, Status.FAILED},
}


class AutomationConflict(ValueError):
    pass


class AutomationNotFound(ValueError):
    pass


def _event(conn, request_id, source, target, actor):
    conn.execute(
        """
        INSERT INTO automation_events (request_id, from_status, to_status, actor_id)
        VALUES (?, ?, ?, ?)
        """,
        (request_id, source, target, actor),
    )


def _snapshot(row):
    if row is None:
        raise AutomationNotFound("자동화 요청을 찾을 수 없습니다.")
    result = dict(row)
    result.pop("idempotency_key", None)
    result["ai_result"] = json.loads(result.pop("ai_result_json") or "null")
    result["fds_result"] = json.loads(result.pop("fds_result_json"))
    result["review_required"] = bool(result["review_required"])
    result["dispatch_pending"] = bool(result["dispatch_pending"])
    return result


def get_request(conn, request_id):
    return _snapshot(conn.execute(
        "SELECT * FROM automation_requests WHERE request_id = ?", (request_id,)
    ).fetchone())


def get_events(conn, request_id):
    return [dict(row) for row in conn.execute(
        """
        SELECT e.event_id, e.from_status, e.to_status, e.actor_id, e.created_at,
               j.job_key, j.job_state, j.result_status, j.incident_id, j.error_message
        FROM automation_events e LEFT JOIN orchestrator_execution_events j USING (event_id)
        WHERE e.request_id = ? ORDER BY e.event_id
        """,
        (request_id,),
    )]


def find_retry(conn, *, actor, idempotency_key, customer_id, conversation):
    row = conn.execute(
        """
        SELECT a.*, c.conversation FROM automation_requests a
        JOIN consultations c ON a.consultation_id = c.consultation_id
        WHERE a.created_by = ? AND a.idempotency_key = ?
        """,
        (actor, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    if (row["customer_id"], row["conversation"]) != (customer_id, conversation):
        raise AutomationConflict("같은 멱등 키에 다른 상담 내용을 사용할 수 없습니다.")
    result = _snapshot(row)
    result.pop("conversation")
    return result


def create_request(conn, *, actor, idempotency_key, customer_id, conversation, ai, fds,
                   on_create=None, retry_guard=None):
    request_id = f"AUTO-{uuid4().hex}"
    consultation_id = f"CONS-{uuid4().hex}"
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        retry = find_retry(conn, actor=actor, idempotency_key=idempotency_key,
                           customer_id=customer_id, conversation=conversation)
        if retry is not None:
            if retry_guard:
                retry_guard(conn, retry["request_id"])
            return retry, False
        conn.execute(
            """
            INSERT INTO consultations
                (consultation_id, customer_id, conversation, ai_summary, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (consultation_id, customer_id, conversation, (ai or {}).get("summary"),
             (ai or {}).get("category")),
        )
        conn.execute(
            """
            INSERT INTO automation_requests
                (request_id, created_by, idempotency_key, customer_id, consultation_id,
                 transaction_id, ai_result_json, fds_result_json, review_required)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (request_id, actor, idempotency_key, customer_id, consultation_id,
             fds["transaction_id"], json.dumps(ai, ensure_ascii=False) if ai is not None else None,
             json.dumps(fds, ensure_ascii=False), int(ai is None or ai["requires_human_review"])),
        )
        _event(conn, request_id, None, Status.PENDING_APPROVAL, actor)
        if on_create:
            on_create(conn, request_id)
        return get_request(conn, request_id), True


def _validate_transition(source, target):
    if target not in TRANSITIONS.get(source, ()):
        raise AutomationConflict("현재 상태에서는 요청된 상태로 변경할 수 없습니다.")


def decide(conn, request_id, target, *, actor, fields, categories, guard=None, on_decide=None):
    if target not in (Status.APPROVED, Status.REJECTED):
        raise AutomationConflict("승인 또는 거부만 기록할 수 있습니다.")
    if target == Status.APPROVED:
        validate_incident_fields({**fields, "request_id": request_id}, categories)
    decision_values = (
        (fields["category"], fields["summary"], fields["risk_level"] or None)
        if target == Status.APPROVED else (None, None, None)
    )

    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = get_request(conn, request_id)
        if guard:
            guard(conn)
        if current["status"] == target and current["decision_by"] == actor:
            previous = tuple(current[name] for name in
                             ("incident_category", "incident_summary", "incident_risk_level"))
            if previous == decision_values:
                return current
        _validate_transition(current["status"], target)
        # APPROVED and its local dispatch intent are committed with the audit event.
        conn.execute(
            """
            UPDATE automation_requests
            SET status = ?, decision_by = ?, decision_at = CURRENT_TIMESTAMP,
                incident_category = ?, incident_summary = ?, incident_risk_level = ?,
                dispatch_pending = ?
            WHERE request_id = ?
            """,
            (target, actor, *decision_values, int(target == Status.APPROVED), request_id),
        )
        _event(conn, request_id, current["status"], target, actor)
        if on_decide:
            on_decide(conn)
        return get_request(conn, request_id)


def record_execution(conn, request_id, target, *, actor, execution_id, incident_id=None):
    """Internal transition for a future trusted execution adapter with verified results."""
    if target not in (Status.QUEUED, Status.RUNNING, Status.SUCCEEDED, Status.FAILED):
        raise AutomationConflict("실행 상태만 기록할 수 있습니다.")
    if not execution_id or (target == Status.SUCCEEDED) != bool(incident_id):
        raise AutomationConflict("실행 식별자 또는 성공 접수번호가 올바르지 않습니다.")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        current = get_request(conn, request_id)
        if (current["status"], current["external_execution_id"], current["incident_id"]) == (
            target, execution_id, incident_id
        ):
            return current
        _validate_transition(current["status"], target)
        if current["external_execution_id"] not in (None, execution_id):
            raise AutomationConflict("기존 실행 식별자와 일치하지 않습니다.")
        if incident_id and not conn.execute(
            """
            SELECT 1 FROM incidents
            WHERE incident_id = ? AND request_id = ? AND customer_id = ? AND consultation_id = ?
            """,
            (incident_id, request_id, current["customer_id"], current["consultation_id"]),
        ).fetchone():
            raise AutomationConflict("해당 업무의 접수번호를 확인할 수 없습니다.")
        conn.execute(
            """
            UPDATE automation_requests
            SET status = ?, external_execution_id = ?, incident_id = ?, dispatch_pending = 0
            WHERE request_id = ?
            """,
            (target, execution_id, incident_id, request_id),
        )
        _event(conn, request_id, current["status"], target, actor)
        return get_request(conn, request_id)
