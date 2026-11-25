import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import timedelta

import click
from flask import (
    Blueprint, abort, current_app, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash


ROLE_PERMISSIONS = {
    "AGENT": frozenset(("create", "read", "decide_own", "dispatch_own")),
    "SUPERVISOR": frozenset(("read", "decide", "dispatch")),
    "ADMIN": frozenset(("create", "read", "decide", "dispatch", "manage_users")),
}

auth = Blueprint("auth", __name__)


def local_enabled():
    return bool(current_app.secret_key)


def local_permission(permission):
    user = g.get("local_user")
    return bool(user and permission in ROLE_PERMISSIONS[user["role"]])


def _connect():
    return current_app.extensions["finguard_db"]()


def csrf_token():
    if not local_enabled():
        return ""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _api_request():
    return request.path.startswith("/api/")


def _verify_csrf():
    supplied = request.headers.get("X-CSRF-Token", "") if request.is_json else request.form.get("csrf_token", "")
    token = session.get("csrf")
    if not (token and supplied and supplied.isascii() and hmac.compare_digest(token, supplied)):
        if _api_request():
            return jsonify({"error": "요청 보안 토큰이 올바르지 않습니다."}), 403
        abort(403)


def _session_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _revoke_session(conn):
    token = session.get("login_token")
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_session_hash(token),))


def _load_identity():
    if (not local_enabled() or request.endpoint == "automation_robot_receipt"
            or request.blueprint == "customer"):
        return
    login_token = session.get("login_token")
    if login_token:
        with closing(_connect()) as conn:
            user = conn.execute(
                """
                SELECT u.username, u.role FROM auth_users u
                JOIN auth_sessions s ON s.username = u.username
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1
                """,
                (_session_hash(login_token), int(time.time())),
            ).fetchone()
        if user is None:
            session.clear()
        else:
            g.local_user = user

    if request.endpoint in (None, "index", "health", "auth.login", "static", "favicon"):
        return
    if not g.get("local_user"):
        if _api_request():
            return jsonify({"error": "로그인이 필요합니다."}), 401
        return redirect(url_for("auth.login"))
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return _verify_csrf()


@auth.route("/login", methods=("GET", "POST"))
def login():
    if not local_enabled():
        abort(503, "서버 세션 키가 설정되지 않았습니다.")
    if request.method == "GET":
        return render_template("crm/login.html", error=None)

    _verify_csrf()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or len(username) > 64 or not password or len(password) > 128:
        return render_template("crm/login.html", error="로그인 정보를 확인해 주세요."), 401
    with closing(_connect()) as conn:
        user = conn.execute(
            "SELECT username, password_hash, active, role FROM auth_users WHERE username = ?",
            (username,),
        ).fetchone()
    password_hash = user["password_hash"] if user else current_app.extensions["finguard_dummy_hash"]
    matches = check_password_hash(password_hash, password)
    if not user or not user["active"] or not matches:
        return render_template("crm/login.html", error="로그인 정보를 확인해 주세요."), 401

    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires_at = now + int(current_app.permanent_session_lifetime.total_seconds())
    with closing(_connect()) as conn, conn:
        _revoke_session(conn)
        customer_token = session.get("customer_login_token")
        if customer_token:
            conn.execute("DELETE FROM customer_sessions WHERE token_hash=?", (_session_hash(customer_token),))
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, username, expires_at) VALUES (?, ?, ?)",
            (_session_hash(token), user["username"], expires_at),
        )
    session.clear()
    session.permanent = True
    session["login_token"] = token
    csrf_token()
    destination = "report_staff.queue" if user["role"] in ("AGENT", "ADMIN") else "consultation_review"
    return redirect(url_for(destination))


@auth.post("/logout")
def logout():
    if not local_enabled():
        abort(503)
    with closing(_connect()) as conn, conn:
        _revoke_session(conn)
    session.clear()
    return redirect(url_for("auth.login"))


@auth.get("/auth/users")
def users():
    if not local_permission("manage_users"):
        abort(403)
    with closing(_connect()) as conn:
        accounts = conn.execute(
            "SELECT username, role, active, created_at FROM auth_users ORDER BY username"
        ).fetchall()
    return render_template("crm/users.html", accounts=accounts)


@auth.post("/auth/users/agent")
def add_agent():
    if not local_permission("manage_users"):
        abort(403)
    username, password = request.form.get("username", "").strip(), request.form.get("password", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username) or not 12 <= len(password) <= 128:
        abort(400, "계정 ID와 비밀번호 조건을 확인해 주세요.")
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO auth_users(username,password_hash,role) VALUES (?,?,'AGENT')",
                (username, generate_password_hash(password)),
            )
    except sqlite3.IntegrityError:
        abort(409, "이미 존재하는 직원 계정입니다.")
    return redirect(url_for("auth.users"), code=303)


@auth.post("/auth/users/<username>/active")
def set_active(username):
    if not local_permission("manage_users"):
        abort(403)
    state = request.form.get("active")
    if state not in ("0", "1"):
        abort(400)
    with closing(_connect()) as conn, conn:
        updated = conn.execute(
            "UPDATE auth_users SET active=? WHERE username=? AND role IN ('AGENT','SUPERVISOR')",
            (int(state), username),
        )
        if updated.rowcount != 1:
            abort(404)
        if state == "0":
            conn.execute("DELETE FROM auth_sessions WHERE username=?", (username,))
    return redirect(url_for("auth.users"), code=303)


@click.command("create-user")
@click.argument("username")
@click.option("--role", type=click.Choice(list(ROLE_PERMISSIONS)), required=True)
@with_appcontext
def create_user(username, role):
    """Explicitly provision an account in this application's database."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise click.ClickException("계정 ID는 영문, 숫자 또는 _.- 1~64자로 입력하세요.")
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if not 12 <= len(password) <= 128:
        raise click.ClickException("비밀번호는 12~128자로 입력하세요.")
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO auth_users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), role),
            )
    except sqlite3.IntegrityError as exc:
        raise click.ClickException("이미 존재하는 계정 ID입니다.") from exc
    except sqlite3.OperationalError as exc:
        raise click.ClickException("DB와 마이그레이션 상태를 확인하세요.") from exc
    click.echo(f"Created account {username} ({role}).")


def configure_auth(app, connect, limiter):
    key = os.getenv("FINGUARD_SECRET_KEY")
    if key and len(key) < 32:
        raise RuntimeError("FINGUARD_SECRET_KEY must have at least 32 characters.")
    app.config.update(
        SECRET_KEY=key,
        SESSION_COOKIE_NAME="finguard_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FINGUARD_SECURE_COOKIE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.extensions["finguard_db"] = connect
    app.extensions["finguard_dummy_hash"] = generate_password_hash(secrets.token_urlsafe(32))
    app.before_request(_load_identity)
    app.context_processor(lambda: {
        "local_auth_enabled": local_enabled(),
        "local_user": g.get("local_user"),
        "csrf_token": csrf_token,
    })
    limiter.limit("10 per minute", exempt_when=lambda: (
        request.endpoint == "auth.login" and request.method in ("GET", "HEAD", "OPTIONS")
    ))(auth)
    app.register_blueprint(auth)
    app.cli.add_command(create_user)
