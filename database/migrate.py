"""Apply data-preserving migrations to an existing FinGuard database."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LEGACY_INCIDENT_COLUMNS = {
    "incident_id", "consultation_id", "transaction_id", "risk_score",
    "risk_level", "status", "rpa_status", "created_at",
}
CURRENT_INCIDENT_COLUMNS = {
    "incident_id", "request_id", "customer_id", "category", "summary", "risk_level",
    "consultation_id", "transaction_id", "status", "created_at",
}


def _execute_script(conn, filename):
    statement = ""
    for line in (BASE_DIR / "migrations" / filename).read_text(encoding="utf-8").splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError(f"Incomplete migration SQL: {filename}")


def apply_migrations(conn):
    if conn.in_transaction:
        raise ValueError("Run migrations outside an active transaction.")
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(incidents)")}
        if columns == LEGACY_INCIDENT_COLUMNS:
            if conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE tbl_name='incidents' AND type IN ('index', 'trigger') AND sql IS NOT NULL
                """
            ).fetchone():
                raise ValueError("기존 incidents 사용자 정의 인덱스 또는 트리거를 검토해 주세요.")
            _execute_script(conn, "000_legacy_incidents.sql")
        elif not CURRENT_INCIDENT_COLUMNS <= columns:
            raise ValueError("지원하는 incidents 스키마와 일치하지 않습니다.")
        _execute_script(conn, "001_automation.sql")
        _execute_script(conn, "002_auth_users.sql")
        _execute_script(conn, "003_auth_sessions.sql")
        _execute_script(conn, "004_orchestrator.sql")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=BASE_DIR / "finguard.db")
    db = parser.parse_args().db.resolve()
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)) as conn:
            required = {"customers", "transactions", "consultations", "incidents"}
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= tables:
                raise ValueError("기존 CRM 테이블이 모두 있는 데이터베이스를 지정해 주세요.")
            apply_migrations(conn)
    except (sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
