"""
api/db.py — MySQL database layer for PayRadar
Connection pool, all queries, full CRUD for every entity.
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "payradar"),
    "password": os.getenv("DB_PASSWORD", "payradar123"),
    "database": os.getenv("DB_NAME", "payradar_db"),
    "charset": "utf8mb4",
    "autocommit": False,
}

_pool: Optional[pooling.MySQLConnectionPool] = None


def _get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="payradar_pool",
            pool_size=10,
            pool_reset_session=True,
            **DB_CONFIG,
        )
    return _pool


def get_connection():
    return _get_pool().get_connection()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_session(session_id: str) -> Optional[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        if row and row["expires_at"] > datetime.utcnow():
            return row
        return None
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM users WHERE username = %s AND is_active = TRUE",
            (username,),
        )
        return cur.fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit log (append-only)
# ---------------------------------------------------------------------------

def log_audit(
    actor: str,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[Dict] = None,
) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO audit_log (actor, action, target_type, target_id, details)
               VALUES (%s, %s, %s, %s, %s)""",
            (actor, action, target_type, target_id, json.dumps(details) if details else None),
        )
        conn.commit()
    except Exception as e:
        logger.error("audit_log insert failed: %s", e)
        conn.rollback()
    finally:
        conn.close()


def get_audit_log(limit: int = 200) -> List[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT %s", (limit,)
        )
        rows = cur.fetchall()
        for r in rows:
            if r.get("details") and isinstance(r["details"], str):
                try:
                    r["details"] = json.loads(r["details"])
                except Exception:
                    pass
            if r.get("timestamp"):
                r["timestamp"] = r["timestamp"].isoformat()
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

def insert_prediction(
    transaction_id: str,
    source: str,
    tx_type: str,
    amount: float,
    orig_account: str,
    dest_account: str,
    ml_probability: float,
    rule_score: float,
    fraud_probability: float,
    decision: str,
    triggered_rules: List[str],
) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO predictions
               (transaction_id, source, type, amount, orig_account, dest_account,
                ml_probability, rule_score, fraud_probability, decision, triggered_rules)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                transaction_id, source, tx_type, amount, orig_account, dest_account,
                round(ml_probability, 4), round(rule_score, 4), round(fraud_probability, 4),
                decision, json.dumps(triggered_rules),
            ),
        )
        # Upsert originating account
        _upsert_account(cur, orig_account, fraud_probability, decision)
        # Upsert destination account
        _upsert_account(cur, dest_account, fraud_probability, decision)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("insert_prediction failed: %s", e)
        raise
    finally:
        conn.close()


def _upsert_account(cur, account_id: str, risk_score: float, decision: str) -> None:
    if not account_id:
        return
    fraud_flag = 1 if decision == "BLOCK" else 0
    cur.execute(
        """INSERT INTO accounts (account_id, total_transactions, total_fraud_flags, avg_risk_score, last_seen)
           VALUES (%s, 1, %s, %s, NOW())
           ON DUPLICATE KEY UPDATE
             total_transactions = total_transactions + 1,
             total_fraud_flags  = total_fraud_flags + %s,
             avg_risk_score     = (avg_risk_score * (total_transactions - 1) + %s) / total_transactions,
             last_seen          = NOW()""",
        (account_id, fraud_flag, round(risk_score, 4), fraud_flag, round(risk_score, 4)),
    )


def fetch_recent_predictions(
    limit: int = 50,
    user_role: str = "analyst",
    username: str = "",
    decision_filter: Optional[str] = None,
    source_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
) -> List[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        conditions = []
        params: List[Any] = []

        if decision_filter:
            conditions.append("decision = %s")
            params.append(decision_filter)
        if source_filter:
            conditions.append("source = %s")
            params.append(source_filter)
        if date_from:
            conditions.append("timestamp >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp <= %s")
            params.append(date_to + " 23:59:59")
        if search:
            conditions.append(
                "(transaction_id LIKE %s OR orig_account LIKE %s OR dest_account LIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        cur.execute(
            f"""SELECT * FROM predictions {where}
                ORDER BY timestamp DESC LIMIT %s""",
            params,
        )
        rows = cur.fetchall()
        for r in rows:
            if r.get("timestamp"):
                r["timestamp"] = r["timestamp"].isoformat()
            if r.get("override_time"):
                r["override_time"] = r["override_time"].isoformat()
            if r.get("triggered_rules") and isinstance(r["triggered_rules"], str):
                try:
                    r["triggered_rules"] = json.loads(r["triggered_rules"])
                except Exception:
                    pass
        return rows
    finally:
        conn.close()


def get_stats() -> Dict:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN decision='APPROVE' THEN 1 ELSE 0 END) AS safe,
                 SUM(CASE WHEN decision='REVIEW'  THEN 1 ELSE 0 END) AS review,
                 SUM(CASE WHEN decision='BLOCK'   THEN 1 ELSE 0 END) AS blocked,
                 AVG(fraud_probability) AS avg_risk,
                 SUM(CASE WHEN decision='BLOCK' THEN amount ELSE 0 END) AS blocked_amount
               FROM predictions"""
        )
        row = cur.fetchone() or {}
        cur.execute(
            """SELECT DATE_FORMAT(timestamp,'%Y-%m-%d %H:00:00') AS hour,
                      COUNT(*) AS count,
                      SUM(CASE WHEN decision='BLOCK' THEN 1 ELSE 0 END) AS fraud_count
               FROM predictions
               WHERE timestamp >= NOW() - INTERVAL 24 HOUR
               GROUP BY hour ORDER BY hour"""
        )
        hourly = cur.fetchall()
        cur.execute(
            """SELECT decision, COUNT(*) AS count
               FROM predictions GROUP BY decision"""
        )
        dist = cur.fetchall()
        return {
            "total": int(row.get("total") or 0),
            "safe": int(row.get("safe") or 0),
            "review": int(row.get("review") or 0),
            "blocked": int(row.get("blocked") or 0),
            "avg_risk": round(float(row.get("avg_risk") or 0), 4),
            "blocked_amount": float(row.get("blocked_amount") or 0),
            "hourly": hourly,
            "distribution": dist,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def get_rules() -> List[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM rule_config ORDER BY rule_id")
        rows = cur.fetchall()
        for r in rows:
            if r.get("last_modified_at"):
                r["last_modified_at"] = r["last_modified_at"].isoformat()
        return rows
    finally:
        conn.close()


def update_rules(rules_list: List[Dict], modified_by: str = "admin") -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        for rule in rules_list:
            cur.execute(
                """UPDATE rule_config
                   SET weight=%s, threshold_value=%s, is_active=%s,
                       last_modified_by=%s, last_modified_at=NOW()
                   WHERE rule_id=%s""",
                (
                    rule.get("weight"),
                    rule.get("threshold_value"),
                    rule.get("is_active", True),
                    modified_by,
                    rule["rule_id"],
                ),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# App settings / thresholds
# ---------------------------------------------------------------------------

def get_thresholds() -> Dict:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT setting_key, setting_value FROM app_settings")
        rows = cur.fetchall()
        result = {r["setting_key"]: float(r["setting_value"]) for r in rows}
        # Defaults if table missing entries
        defaults = {
            "approve_threshold": 0.40,
            "review_threshold": 0.55,
            "block_threshold": 0.70,
            "critical_threshold": 0.85,
            "ml_weight": 0.60,
            "rules_weight": 0.40,
        }
        for k, v in defaults.items():
            result.setdefault(k, v)
        return result
    except Exception:
        return {
            "approve_threshold": 0.40,
            "review_threshold": 0.55,
            "block_threshold": 0.70,
            "critical_threshold": 0.85,
            "ml_weight": 0.60,
            "rules_weight": 0.40,
        }
    finally:
        conn.close()


def update_thresholds(settings: Dict, modified_by: str = "admin") -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        for key, value in settings.items():
            cur.execute(
                """INSERT INTO app_settings (setting_key, setting_value, last_modified_by, last_modified_at)
                   VALUES (%s, %s, %s, NOW())
                   ON DUPLICATE KEY UPDATE
                     setting_value = %s, last_modified_by = %s, last_modified_at = NOW()""",
                (key, str(value), modified_by, str(value), modified_by),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def get_accounts(status_filter: Optional[str] = None, search: Optional[str] = None) -> List[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        conditions = []
        params: List[Any] = []
        if status_filter:
            conditions.append("status = %s")
            params.append(status_filter)
        if search:
            conditions.append("account_id LIKE %s")
            params.append(f"%{search}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        cur.execute(f"SELECT * FROM accounts {where} ORDER BY last_seen DESC LIMIT 200", params)
        rows = cur.fetchall()
        for r in rows:
            for dt_field in ("first_seen", "last_seen", "frozen_at"):
                if r.get(dt_field):
                    r[dt_field] = r[dt_field].isoformat()
        return rows
    finally:
        conn.close()


def get_account(account_id: str) -> Optional[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM accounts WHERE account_id = %s", (account_id,))
        row = cur.fetchone()
        if not row:
            return None
        for dt_field in ("first_seen", "last_seen", "frozen_at"):
            if row.get(dt_field):
                row[dt_field] = row[dt_field].isoformat()
        # Recent transactions for this account
        cur.execute(
            """SELECT transaction_id, timestamp, type, amount, fraud_probability, decision
               FROM predictions
               WHERE orig_account = %s OR dest_account = %s
               ORDER BY timestamp DESC LIMIT 20""",
            (account_id, account_id),
        )
        txns = cur.fetchall()
        for t in txns:
            if t.get("timestamp"):
                t["timestamp"] = t["timestamp"].isoformat()
        row["recent_transactions"] = txns
        return row
    finally:
        conn.close()


def freeze_account(account_id: str, frozen_by: str, reason: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE accounts
               SET status='Frozen', frozen_by=%s, freeze_reason=%s, frozen_at=NOW()
               WHERE account_id=%s""",
            (frozen_by, reason, account_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def unfreeze_account(account_id: str, unfrozen_by: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE accounts
               SET status='Active', frozen_by=NULL, freeze_reason=NULL, frozen_at=NULL
               WHERE account_id=%s""",
            (account_id,),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_velocity(account_id: str, window_minutes: int = 10) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT COUNT(*) FROM predictions
               WHERE orig_account = %s
               AND timestamp >= NOW() - INTERVAL %s MINUTE""",
            (account_id, window_minutes),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def get_cases(
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    assigned_to: Optional[str] = None,
    limit: int = 100,
) -> List[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        conditions = []
        params: List[Any] = []
        if status_filter:
            conditions.append("c.status = %s")
            params.append(status_filter)
        if priority_filter:
            conditions.append("c.priority = %s")
            params.append(priority_filter)
        if assigned_to:
            conditions.append("c.assigned_to = %s")
            params.append(assigned_to)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        cur.execute(
            f"""SELECT c.*, p.amount, p.type, p.fraud_probability, p.decision AS tx_decision
                FROM cases c
                LEFT JOIN predictions p ON c.transaction_id = p.transaction_id
                {where}
                ORDER BY c.opened_at DESC LIMIT %s""",
            params,
        )
        rows = cur.fetchall()
        for r in rows:
            for dt_field in ("opened_at", "resolved_at"):
                if r.get(dt_field):
                    r[dt_field] = r[dt_field].isoformat()
        return rows
    finally:
        conn.close()


def create_case(transaction_id: str, priority: str = "Medium") -> Dict:
    import random
    import string
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        # Check if case already exists for this transaction
        cur.execute("SELECT * FROM cases WHERE transaction_id = %s", (transaction_id,))
        existing = cur.fetchone()
        if existing:
            if existing.get("opened_at"):
                existing["opened_at"] = existing["opened_at"].isoformat()
            return existing
        case_number = "CASE-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
        cur.execute(
            """INSERT INTO cases (case_number, transaction_id, priority)
               VALUES (%s, %s, %s)""",
            (case_number, transaction_id, priority),
        )
        conn.commit()
        case_id = cur.lastrowid
        return {"id": case_id, "case_number": case_number, "transaction_id": transaction_id, "priority": priority}
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_case(case_id: int, updates: Dict, actor: str = "") -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        allowed = ["status", "assigned_to", "priority", "resolution"]
        set_parts = []
        params: List[Any] = []
        for field in allowed:
            if field in updates and updates[field] is not None:
                set_parts.append(f"{field} = %s")
                params.append(updates[field])
        if updates.get("status") == "Resolved":
            set_parts.append("resolved_at = NOW()")
        if not set_parts:
            return
        params.append(case_id)
        cur.execute(
            f"UPDATE cases SET {', '.join(set_parts)} WHERE id = %s", params
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_case_note(case_id: int, author: str, note: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO case_notes (case_id, author, note) VALUES (%s, %s, %s)",
            (case_id, author, note),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_case_detail(case_id: int) -> Optional[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT c.*, p.amount, p.type, p.fraud_probability, p.decision AS tx_decision,
                      p.triggered_rules, p.orig_account, p.dest_account, p.timestamp AS tx_timestamp
               FROM cases c
               LEFT JOIN predictions p ON c.transaction_id = p.transaction_id
               WHERE c.id = %s""",
            (case_id,),
        )
        case = cur.fetchone()
        if not case:
            return None
        for dt_field in ("opened_at", "resolved_at"):
            if case.get(dt_field):
                case[dt_field] = case[dt_field].isoformat()
        if case.get("tx_timestamp"):
            case["tx_timestamp"] = case["tx_timestamp"].isoformat()
        if case.get("triggered_rules") and isinstance(case["triggered_rules"], str):
            try:
                case["triggered_rules"] = json.loads(case["triggered_rules"])
            except Exception:
                pass
        cur.execute(
            "SELECT * FROM case_notes WHERE case_id = %s ORDER BY created_at ASC",
            (case_id,),
        )
        notes = cur.fetchall()
        for n in notes:
            if n.get("created_at"):
                n["created_at"] = n["created_at"].isoformat()
        case["notes"] = notes
        return case
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Override decision
# ---------------------------------------------------------------------------

def override_decision(
    transaction_id: str,
    new_decision: str,
    override_by: str,
    reason: str,
) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE predictions
               SET is_overridden=TRUE, override_by=%s, override_reason=%s,
                   override_time=NOW(), original_decision=decision, decision=%s
               WHERE transaction_id=%s""",
            (override_by, reason, new_decision, transaction_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_users() -> List[Dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, username, role, full_name, email, is_active, created_at, last_login, created_by FROM users ORDER BY id"
        )
        rows = cur.fetchall()
        for r in rows:
            for dt_field in ("created_at", "last_login"):
                if r.get(dt_field):
                    r[dt_field] = r[dt_field].isoformat()
        return rows
    finally:
        conn.close()


def create_user(
    username: str,
    password_hash: str,
    role: str,
    full_name: str,
    email: str,
    created_by: str,
) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO users (username, password_hash, role, full_name, email, created_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (username, password_hash, role, full_name, email, created_by),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def deactivate_user(user_id: int) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_active=FALSE WHERE id=%s", (user_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_user(user_id: int, updates: Dict) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        allowed = ["full_name", "email", "role", "is_active"]
        set_parts = []
        params: List[Any] = []
        for field in allowed:
            if field in updates:
                set_parts.append(f"{field} = %s")
                params.append(updates[field])
        if not set_parts:
            return
        params.append(user_id)
        cur.execute(f"UPDATE users SET {', '.join(set_parts)} WHERE id = %s", params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Model stats
# ---------------------------------------------------------------------------

def get_model_stats() -> Dict:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT
                 COUNT(*) AS total_predictions,
                 SUM(CASE WHEN decision='BLOCK' THEN 1 ELSE 0 END) AS total_blocked,
                 SUM(CASE WHEN decision='REVIEW' THEN 1 ELSE 0 END) AS total_review,
                 SUM(CASE WHEN decision='APPROVE' THEN 1 ELSE 0 END) AS total_approved,
                 SUM(CASE WHEN is_overridden=TRUE THEN 1 ELSE 0 END) AS total_overrides,
                 AVG(fraud_probability) AS avg_fraud_prob
               FROM predictions"""
        )
        row = cur.fetchone() or {}
        cur.execute(
            """SELECT DATE(timestamp) AS date,
                      SUM(CASE WHEN decision='APPROVE' THEN 1 ELSE 0 END) AS approved,
                      SUM(CASE WHEN decision='REVIEW'  THEN 1 ELSE 0 END) AS review,
                      SUM(CASE WHEN decision='BLOCK'   THEN 1 ELSE 0 END) AS blocked
               FROM predictions
               WHERE timestamp >= NOW() - INTERVAL 30 DAY
               GROUP BY date ORDER BY date"""
        )
        daily = cur.fetchall()
        for d in daily:
            if d.get("date"):
                d["date"] = d["date"].isoformat()
        cur.execute(
            """SELECT triggered_rules FROM predictions
               WHERE triggered_rules IS NOT NULL
               AND timestamp >= NOW() - INTERVAL 30 DAY"""
        )
        rule_rows = cur.fetchall()
        rule_counts: Dict[str, int] = {}
        for rr in rule_rows:
            rules_val = rr.get("triggered_rules")
            if isinstance(rules_val, str):
                try:
                    rules_val = json.loads(rules_val)
                except Exception:
                    rules_val = []
            if isinstance(rules_val, list):
                for rule in rules_val:
                    rule_counts[rule] = rule_counts.get(rule, 0) + 1
        total = int(row.get("total_predictions") or 0)
        total_blocked = int(row.get("total_blocked") or 0)
        total_overrides = int(row.get("total_overrides") or 0)
        return {
            "total_predictions": total,
            "total_blocked": total_blocked,
            "total_review": int(row.get("total_review") or 0),
            "total_approved": int(row.get("total_approved") or 0),
            "total_overrides": total_overrides,
            "avg_fraud_prob": round(float(row.get("avg_fraud_prob") or 0), 4),
            "override_rate": round(total_overrides / max(total, 1), 4),
            "false_positive_rate": 0.0,
            "daily_distribution": daily,
            "rule_trigger_counts": rule_counts,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DB init (inline DDL)
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    ddl_statements = [
        """CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role ENUM('admin','analyst') NOT NULL,
            full_name VARCHAR(100),
            email VARCHAR(100),
            is_active BOOLEAN DEFAULT TRUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME,
            created_by VARCHAR(50)
        )""",
        """CREATE TABLE IF NOT EXISTS sessions (
            session_id VARCHAR(64) PRIMARY KEY,
            user_id INT NOT NULL,
            username VARCHAR(50) NOT NULL,
            role ENUM('admin','analyst') NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        """CREATE TABLE IF NOT EXISTS predictions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            transaction_id VARCHAR(8) UNIQUE NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            source ENUM('manual','simulated') DEFAULT 'manual',
            type VARCHAR(20),
            amount DECIMAL(15,2),
            orig_account VARCHAR(50),
            dest_account VARCHAR(50),
            ml_probability DECIMAL(5,4),
            rule_score DECIMAL(5,4),
            fraud_probability DECIMAL(5,4),
            decision ENUM('APPROVE','REVIEW','BLOCK') NOT NULL,
            triggered_rules JSON,
            is_overridden BOOLEAN DEFAULT FALSE,
            override_by VARCHAR(50),
            override_reason TEXT,
            override_time DATETIME,
            original_decision ENUM('APPROVE','REVIEW','BLOCK')
        )""",
        """CREATE TABLE IF NOT EXISTS cases (
            id INT AUTO_INCREMENT PRIMARY KEY,
            case_number VARCHAR(12) UNIQUE NOT NULL,
            transaction_id VARCHAR(8) NOT NULL,
            status ENUM('Open','Under Investigation','Escalated','Resolved') DEFAULT 'Open',
            assigned_to VARCHAR(50),
            priority ENUM('Low','Medium','High','Critical') DEFAULT 'Medium',
            resolution ENUM('Confirmed Fraud','False Positive','Inconclusive'),
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME,
            FOREIGN KEY (transaction_id) REFERENCES predictions(transaction_id)
        )""",
        """CREATE TABLE IF NOT EXISTS case_notes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            case_id INT NOT NULL,
            author VARCHAR(50) NOT NULL,
            note TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES cases(id)
        )""",
        """CREATE TABLE IF NOT EXISTS accounts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            account_id VARCHAR(50) UNIQUE NOT NULL,
            status ENUM('Active','Frozen','Under Review') DEFAULT 'Active',
            total_transactions INT DEFAULT 0,
            total_fraud_flags INT DEFAULT 0,
            avg_risk_score DECIMAL(5,4) DEFAULT 0,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            frozen_by VARCHAR(50),
            freeze_reason TEXT,
            frozen_at DATETIME
        )""",
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            actor VARCHAR(50) NOT NULL,
            action VARCHAR(100) NOT NULL,
            target_type VARCHAR(50),
            target_id VARCHAR(50),
            details JSON,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS rule_config (
            id INT AUTO_INCREMENT PRIMARY KEY,
            rule_id VARCHAR(10) UNIQUE NOT NULL,
            rule_name VARCHAR(100) NOT NULL,
            description TEXT,
            weight DECIMAL(4,2) NOT NULL,
            threshold_value DECIMAL(15,2),
            is_active BOOLEAN DEFAULT TRUE,
            last_modified_by VARCHAR(50),
            last_modified_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS app_settings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            setting_key VARCHAR(100) UNIQUE NOT NULL,
            setting_value VARCHAR(255) NOT NULL,
            last_modified_by VARCHAR(50),
            last_modified_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    seed_rules = """INSERT IGNORE INTO rule_config
        (rule_id, rule_name, description, weight, threshold_value) VALUES
        ('R1','High Amount','Transaction amount exceeds threshold',0.25,100000.00),
        ('R2','Odd Hour','Transaction between 2AM - 5AM',0.15,NULL),
        ('R3','Balance Drain','Sender balance dropped to zero',0.30,NULL),
        ('R4','Destination Anomaly','Destination balance unchanged after transaction',0.20,NULL),
        ('R5','High Risk Type','Transaction is TRANSFER or CASH_OUT',0.10,NULL),
        ('R6','Velocity Attack','More than 5 transactions in 10 minutes',0.35,5.00),
        ('R7','Frozen Account','Source account is currently frozen',1.00,NULL)"""
    seed_settings = """INSERT IGNORE INTO app_settings (setting_key, setting_value) VALUES
        ('approve_threshold','0.40'),
        ('review_threshold','0.55'),
        ('block_threshold','0.70'),
        ('critical_threshold','0.85'),
        ('ml_weight','0.60'),
        ('rules_weight','0.40')"""
    seed_users = """INSERT IGNORE INTO users (username, password_hash, role, full_name, email) VALUES
        ('admin','$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMnqxQHX0TJqKHIFJjO5dXq3lK','admin','System Administrator','admin@payradar.bank'),
        ('analyst','$2b$12$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uHezpACEa','analyst','Fraud Analyst','analyst@payradar.bank')"""

    conn = get_connection()
    try:
        cur = conn.cursor()
        for stmt in ddl_statements:
            cur.execute(stmt)
        cur.execute(seed_rules)
        cur.execute(seed_settings)
        cur.execute(seed_users)
        conn.commit()
        logger.info("Database initialized successfully.")
    except Exception as e:
        conn.rollback()
        logger.error("init_db failed: %s", e)
        raise
    finally:
        conn.close()
