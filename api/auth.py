"""
api/auth.py — Session management and password hashing
"""
import os
import secrets
import bcrypt
from datetime import datetime, timedelta
from fastapi import Request
from fastapi.responses import RedirectResponse
from api.db import get_connection

SESSION_EXPIRE_HOURS = int(os.getenv("SESSION_EXPIRE_HOURS", 8))
COOKIE_NAME = "payradar_session"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session(user_id: int, username: str, role: str) -> str:
    session_id = secrets.token_hex(32)
    expires_at = datetime.utcnow() + timedelta(hours=SESSION_EXPIRE_HOURS)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (session_id, user_id, username, role, expires_at) VALUES (%s,%s,%s,%s,%s)",
            (session_id, user_id, username, role, expires_at)
        )
        cur.execute("UPDATE users SET last_login=%s WHERE id=%s", (datetime.utcnow(), user_id))
        conn.commit()
    finally:
        conn.close()
    return session_id


def get_session(session_id: str) -> dict | None:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM sessions WHERE session_id=%s", (session_id,))
        row = cur.fetchone()
        if row and row["expires_at"] > datetime.utcnow():
            return row
        return None
    finally:
        conn.close()


def delete_session(session_id: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE session_id=%s", (session_id,))
        conn.commit()
    finally:
        conn.close()


def get_current_user(request: Request) -> dict | None:
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        return None
    return get_session(session_id)


def require_auth(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise RedirectException("/login")
    return user


def require_admin(request: Request) -> dict:
    user = require_auth(request)
    if user["role"] != "admin":
        from fastapi import HTTPException
        raise HTTPException(403, "Admin access required")
    return user


class RedirectException(Exception):
    def __init__(self, url: str):
        self.url = url
