import sqlite3
from datetime import datetime
from uuid import uuid4


RISK_LEVELS = {"LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음"}


class IncidentValidationError(ValueError):
    pass


def validate_incident_fields(fields, categories):
    if len(fields["request_id"]) > 50:
        raise IncidentValidationError("요청 ID는 50자 이하로 입력해 주세요.")
    if fields["category"] not in categories:
        raise IncidentValidationError("사고 유형을 선택해 주세요.")
    if not fields["summary"] or len(fields["summary"]) > 2000:
        raise IncidentValidationError("상담 요약을 1~2000자로 입력해 주세요.")
    if fields["risk_level"] and fields["risk_level"] not in RISK_LEVELS:
        raise IncidentValidationError("위험도 값이 올바르지 않습니다.")


def create_incident(conn, fields, *, categories, uuid_factory=uuid4):
    """Create a legacy CRM incident or return its existing ID on a retry.

    Managed automation IDs are reserved for the authorized robot endpoint.
    This entry point owns the transaction and expects a fresh connection.
    """
    validate_incident_fields(fields, categories)
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = _insert_incident(conn, fields, uuid_factory)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def _existing_incident(conn, request_id):
    return conn.execute(
        "SELECT incident_id FROM incidents WHERE request_id = ?", (request_id,)
    ).fetchone()


def create_robot_incident(conn, snapshot, *, categories, allow_insert):
    """Use the approved snapshot inside the robot endpoint's write transaction."""
    fields = {
        "request_id": snapshot["request_id"],
        "customer_id": snapshot["customer_id"],
        "category": snapshot["incident_category"],
        "summary": snapshot["incident_summary"],
        "risk_level": snapshot["incident_risk_level"],
        "consultation_id": snapshot["consultation_id"],
        "transaction_id": snapshot["transaction_id"],
    }
    existing = conn.execute(
        "SELECT incident_id, customer_id, consultation_id FROM incidents WHERE request_id = ?",
        (fields["request_id"],),
    ).fetchone()
    if existing:
        if (existing["customer_id"], existing["consultation_id"]) != (
            fields["customer_id"], fields["consultation_id"]
        ):
            raise IncidentValidationError("기존 접수와 승인 스냅샷이 일치하지 않습니다.")
        return existing["incident_id"], True
    if not allow_insert:
        raise IncidentValidationError("실행 중인 요청만 접수할 수 있습니다.")
    validate_incident_fields(fields, categories)
    return _insert_incident(conn, fields, uuid4, managed=True)


def _insert_incident(conn, fields, uuid_factory, *, managed=False):
    request_id = fields["request_id"]
    if not conn.execute(
        "SELECT 1 FROM customers WHERE customer_id = ?", (fields["customer_id"],)
    ).fetchone():
        raise IncidentValidationError("존재하지 않는 고객 ID입니다.")

    if request_id:
        existing = _existing_incident(conn, request_id)
        if existing:
            return existing["incident_id"], True
        if request_id.upper().startswith("AUTO-") and not managed:
            raise IncidentValidationError("자동화 요청은 이 접수 경로에서 처리할 수 없습니다.")

    for _ in range(3):
        incident_id = f"INC-{datetime.now():%Y%m%d}-{uuid_factory().hex[:6].upper()}"
        inserted = conn.execute(
            """
            INSERT INTO incidents
                (incident_id, request_id, customer_id, category, summary, risk_level,
                 consultation_id, transaction_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (incident_id, request_id or None, fields["customer_id"], fields["category"],
             fields["summary"], fields["risk_level"] or None,
             fields["consultation_id"] if managed else None,
             fields["transaction_id"] if managed else None),
        )
        if inserted.rowcount:
            return incident_id, False
        if request_id:
            existing = _existing_incident(conn, request_id)
            if existing:
                return existing["incident_id"], True

    raise sqlite3.OperationalError("incident insert conflicted 3 times")
