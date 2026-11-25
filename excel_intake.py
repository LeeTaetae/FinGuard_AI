"""Local Excel-backed intake site, run independently on port 5001."""

import hmac
import io
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4
from zipfile import BadZipFile

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from itsdangerous import BadSignature, URLSafeTimedSerializer
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException

from database.category_labels import CATEGORY_LABELS

ROOT = Path(__file__).resolve().parent
COLUMNS = ("접수 시각 (한국 시간)", "요청 ID", "CRM 접수번호", "카테고리", "위험도", "접수 내용")
PALETTE = ("#265cc5", "#118377", "#a75e04", "#8b52af", "#66778f")
RISK = {"": "미지정", "LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음"}
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def normalize(data, *, robot):
    values = []
    limits = {"request_id": 37, "incident_id": 50, "category": 64, "risk_level": 12, "summary": 2000}
    for key, limit in limits.items():
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > limit or ILLEGAL_CHARACTERS_RE.search(value):
            raise ValueError
        values.append(value.strip())
    request_id, incident_id, category, risk, summary = values
    prefix = "AUTO-" if robot else "XLS-"
    if not re.fullmatch(prefix + r"[a-f0-9]{32}", request_id):
        raise ValueError
    if (robot and not re.fullmatch(r"INC-[0-9]{8}-[A-Z0-9]+", incident_id)) or (not robot and incident_id):
        raise ValueError
    if category not in CATEGORY_LABELS or risk not in RISK or not summary:
        raise ValueError
    return request_id, incident_id, category, risk, summary


@contextmanager
def workbook_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        expires = time.monotonic() + 1
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= expires:
                    raise
                time.sleep(0.05)
        yield


def new_workbook():
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "접수 목록"
    worksheet.append(COLUMNS)
    for cell in worksheet[1]:
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.font = Font(color="FFFFFF", bold=True)
    for column, width in zip("ABCDEF", (24, 43, 24, 23, 12, 64)):
        worksheet.column_dimensions[column].width = width
    worksheet.freeze_panes = "B2"
    return workbook


def save_row(path, values):
    with workbook_lock(path):
        workbook = load_workbook(path) if path.exists() else new_workbook()
        try:
            sheet = workbook["접수 목록"]
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if row[1] == values[0]:
                    if tuple(value or "" for value in row[1:6]) != values:
                        abort(409, "같은 요청 ID에 다른 내용이 이미 저장되어 있습니다.")
                    return False

            timestamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
            sheet.append((timestamp, *values))
            for cell in sheet[sheet.max_row]:
                # Text beginning with =, +, - or @ must stay literal in Excel.
                cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=cell.column == 6)
            sheet.auto_filter.ref = sheet.dimensions
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
            try:
                workbook.save(temp_path)
                temp_path.replace(path)
            finally:
                temp_path.unlink(missing_ok=True)
            return True
        finally:
            workbook.close()


def read_rows(path):
    with workbook_lock(path):
        if not path.exists():
            return []
        workbook = load_workbook(path, read_only=True)
        try:
            return list(workbook["접수 목록"].iter_rows(min_row=2, values_only=True))[::-1]
        finally:
            workbook.close()


def create_app(*, token, workbook_path):
    if not token or len(token) < 32 or not token.isascii():
        raise ValueError("FINGUARD_EXCEL_INTAKE_TOKEN에는 32자 이상의 ASCII 값을 설정하세요.")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=hmac.digest(token.encode(), b"FinGuard Excel intake session", "sha256"),
        SESSION_COOKIE_NAME="finguard_excel_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=32768,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")
    path = Path(workbook_path)
    drafts = path.parent / "drafts"
    handoff = URLSafeTimedSerializer(app.secret_key, salt="excel-draft-handoff")
    redeemed = {}
    redemption_lock = Lock()
    handoff_seconds = 30

    @app.context_processor
    def template_context():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf": session["csrf"], "categories": CATEGORY_LABELS, "risks": RISK}

    @app.before_request
    def access():
        if request.endpoint in ("static", None, "submit_robot", "draft_ready", "open_draft", "health"):
            return
        if request.method == "POST":
            supplied = request.form.get("csrf", "")
            expected = session.get("csrf", "")
            if not expected or not supplied.isascii() or not hmac.compare_digest(supplied, expected):
                abort(403)
        scoped_draft = request.endpoint == "review_draft" and (
            session.get("draft_access") == request.view_args["request_id"]
        )
        if request.endpoint != "login" and not (session.get("signed_in") or scoped_draft):
            if request.endpoint == "review_draft":
                session["return_draft"] = request.view_args["request_id"]
            return redirect(url_for("login"))

    @app.after_request
    def private_page(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(BadZipFile)
    @app.errorhandler(InvalidFileException)
    @app.errorhandler(OSError)
    def workbook_unavailable(_):
        message = "엑셀 파일을 사용할 수 없습니다. 파일이 Excel에서 열려 있다면 닫고 다시 시도해 주세요."
        if request.endpoint == "submit_robot":
            return jsonify(error=message), 503
        return render_template("excel_intake/page.html", view="error", error=message), 503

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("10 per minute", methods=["POST"])
    def login():
        if request.method == "POST":
            supplied = request.form.get("password", "")
            if supplied.isascii() and hmac.compare_digest(supplied, token):
                draft = session.get("return_draft")
                session.clear()
                session.permanent = True
                session["signed_in"] = True
                target = url_for("review_draft", request_id=draft) if draft else url_for("intake")
                return redirect(target, code=303)
            return render_template("excel_intake/page.html", view="login", error="접근 토큰을 확인해 주세요."), 401
        return render_template("excel_intake/page.html", view="login")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), code=303)

    @app.route("/", methods=["GET", "POST"])
    def intake():
        if request.method == "GET":
            pending = sorted(draft.stem for draft in drafts.glob("AUTO-*.json"))
            return render_template("excel_intake/page.html", view="form", request_id=f"XLS-{uuid4().hex}",
                                   pending=pending)
        try:
            values = normalize(request.form, robot=False)
        except ValueError:
            return render_template("excel_intake/page.html", view="form", request_id=f"XLS-{uuid4().hex}",
                                   form=request.form, error="카테고리와 접수 내용을 확인해 주세요."), 400
        save_row(path, values)
        return redirect(url_for("records"), code=303)

    @app.route("/drafts/<request_id>", methods=["GET", "POST"])
    def review_draft(request_id):
        if not re.fullmatch(r"AUTO-[a-f0-9]{32}", request_id):
            abort(404)
        draft = drafts / f"{request_id}.json"
        with workbook_lock(draft):
            if not draft.exists():
                if any(row[1] == request_id for row in read_rows(path)):
                    if session.get("signed_in"):
                        return redirect(url_for("records"), code=303)
                    return render_template("excel_intake/page.html", view="saved", request_id=request_id)
                abort(404)
            values = json.loads(draft.read_text(encoding="utf-8"))
            if request.method == "POST":
                save_row(path, tuple(values))
                draft.unlink()
                endpoint = url_for("records") if session.get("signed_in") else url_for(
                    "review_draft", request_id=request_id,
                )
                return redirect(endpoint, code=303)
        return render_template("excel_intake/page.html", view="form", request_id=request_id,
                               incident_id=values[1],
                               form=dict(zip(("category", "risk_level", "summary"), values[2:])))

    def robot_authorized():
        value = request.headers.get("Authorization", "")
        return value.isascii() and hmac.compare_digest(value, f"Bearer {token}")

    @app.get("/api/drafts/<request_id>")
    def draft_ready(request_id):
        if not robot_authorized():
            abort(401)
        if not re.fullmatch(r"AUTO-[a-f0-9]{32}", request_id):
            abort(404)
        if not (drafts / f"{request_id}.json").is_file() and not any(
            row[1] == request_id for row in read_rows(path)
        ):
            abort(404)
        return jsonify(ticket=handoff.dumps([request_id, secrets.token_urlsafe(24)]))

    @app.post("/drafts/open")
    def open_draft():
        try:
            request_id, nonce = handoff.loads(request.form.get("ticket", ""), max_age=handoff_seconds)
        except BadSignature:
            abort(403)
        with redemption_lock:
            now = time.monotonic()
            for key in tuple(redeemed):
                if redeemed[key] <= now:
                    del redeemed[key]
            if nonce in redeemed:
                abort(403)
            redeemed[nonce] = now + handoff_seconds
        if not session.get("signed_in"):
            session.clear()
            session["draft_access"] = request_id
        return redirect(url_for("review_draft", request_id=request_id), code=303)

    @app.post("/api/drafts")
    def submit_robot():
        if not robot_authorized():
            return jsonify(error="로봇 인증이 필요합니다."), 401
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise ValueError
            values = normalize(data, robot=True)
        except ValueError:
            return jsonify(error="접수 데이터 형식을 확인해 주세요."), 400
        draft = drafts / f"{values[0]}.json"
        with workbook_lock(draft):
            existing = next((row[1:6] for row in read_rows(path) if row[1] == values[0]), None)
            if existing is None and draft.exists():
                existing = json.loads(draft.read_text(encoding="utf-8"))
            if existing is not None:
                if tuple(value or "" for value in existing) != values:
                    return jsonify(error="같은 요청 ID에 다른 접수 내용이 있습니다."), 409
                return jsonify(request_id=values[0], status="DUPLICATE")
            with tempfile.NamedTemporaryFile(dir=drafts, suffix=".tmp", delete=False) as temp:
                temp_path = Path(temp.name)
            try:
                temp_path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
                temp_path.replace(draft)
            finally:
                temp_path.unlink(missing_ok=True)
        return jsonify(request_id=values[0], status="PENDING_SAVE"), 201

    @app.get("/records")
    def records():
        rows = read_rows(path)
        counts = {key: 0 for key in CATEGORY_LABELS}
        for row in rows:
            if row[3] in counts:
                counts[row[3]] += 1
        total = sum(counts.values())
        stats, segments = [], []
        cumulative = 0
        for (key, count), color in zip(counts.items(), PALETTE):
            percent = count * 100 / total if total else 0
            stats.append((CATEGORY_LABELS[key], count, percent, color))
            if count:
                segments.append(f"{color} {cumulative:.8f}% {cumulative + percent:.8f}%")
                cumulative += percent
        return render_template("excel_intake/page.html", view="records", rows=rows, total=total,
                               stats=stats, chart=", ".join(segments))

    @app.get("/records.xlsx")
    def download():
        with workbook_lock(path):
            if path.exists():
                content = io.BytesIO(path.read_bytes())
            else:
                content = io.BytesIO()
                workbook = new_workbook()
                workbook.save(content)
                workbook.close()
                content.seek(0)
        return send_file(content, mimetype=XLSX_TYPE, as_attachment=True, download_name="FinGuard-접수목록.xlsx")

    return app


if __name__ == "__main__":
    load_dotenv(ROOT / ".env.local")
    load_dotenv(ROOT / ".env")
    try:
        app = create_app(token=os.getenv("FINGUARD_EXCEL_INTAKE_TOKEN"),
                         workbook_path=ROOT / ".uipath" / "excel-intake" / "receipts.xlsx")
    except ValueError as error:
        raise SystemExit(str(error)) from None
    app.run(host="127.0.0.1", port=5001)
