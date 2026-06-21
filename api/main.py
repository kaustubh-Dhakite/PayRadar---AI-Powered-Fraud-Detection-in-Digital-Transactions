"""
api/main.py — PayRadar FastAPI application
All endpoints: HTML pages + REST API
"""
from __future__ import annotations

import json
import logging
import os
import random
import secrets
import string
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import joblib
import numpy as np
import pandas as pd
import shap
from fastapi import FastAPI, HTTPException, Query, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.features import prepare_features_for_model, get_model_feature_columns
from api.schemas import (
    AccountFreeze, CaseCreate, CaseUpdate, Decision,
    HealthResponse, LoginRequest, OverrideRequest,
    PredictionResponse, RuleUpdate, ShapFeature,
    ThresholdUpdate, TransactionInput, UserCreate,
)
from api import db as DB
from api.auth import (
    COOKIE_NAME, RedirectException,
    create_session, delete_session,
    get_current_user, require_admin, require_auth,
    verify_password,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────
MODEL_PATH  = os.path.join(ROOT, "models", "fraud_model.pkl")
SCALER_PATH = os.path.join(ROOT, "models", "scaler.pkl")
META_PATH   = os.path.join(ROOT, "models", "model_meta.json")
FEAT_PATH   = os.path.join(ROOT, "models", "feature_columns.json")

# ── Globals ───────────────────────────────────────────────────────────────
_model         = None
_scaler        = None
_explainer     = None
_model_version = "1.0.0"
_feature_cols  = []
_rules_cache   = []
_thresholds    = {}


def _load_artifacts():
    global _model, _scaler, _explainer, _model_version, _feature_cols
    for p in [MODEL_PATH, SCALER_PATH]:
        if not os.path.exists(p):
            raise RuntimeError(f"Artifact missing: {p}. Run src/train.py first.")
    _model  = joblib.load(MODEL_PATH)
    _scaler = joblib.load(SCALER_PATH)
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            _model_version = json.load(f).get("model_version", "1.0.0")
    if os.path.exists(FEAT_PATH):
        with open(FEAT_PATH) as f:
            _feature_cols = json.load(f).get("feature_columns", get_model_feature_columns())
    _explainer = shap.TreeExplainer(_model)
    logger.info("Model v%s loaded. SHAP explainer ready.", _model_version)


def _reload_rules():
    global _rules_cache
    try:
        _rules_cache = DB.get_rules()
    except Exception as e:
        logger.warning("Could not load rules from DB: %s", e)
        _rules_cache = []


def _reload_thresholds():
    global _thresholds
    try:
        _thresholds = DB.get_thresholds()
    except Exception as e:
        logger.warning("Could not load thresholds: %s", e)
        _thresholds = {
            "approve_threshold": 0.40, "block_threshold": 0.70,
            "critical_threshold": 0.85, "ml_weight": 0.60, "rules_weight": 0.40,
        }


# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="PayRadar", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR    = os.path.join(ROOT, "static")
TEMPLATES_DIR = os.path.join(ROOT, "templates")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.exception_handler(RedirectException)
async def redirect_handler(request: Request, exc: RedirectException):
    return RedirectResponse(exc.url, status_code=302)


@app.on_event("startup")
def startup():
    _load_artifacts()
    try:
        DB.init_db()
        _reload_rules()
        _reload_thresholds()
        logger.info("DB ready.")
    except Exception as e:
        logger.warning("DB init failed (app will still start): %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────

def _scale(X: np.ndarray) -> np.ndarray:
    """Apply the loaded dict-based scaler."""
    return (X - _scaler["mean"]) / _scaler["std"]


def _decide(score: float) -> str:
    bt = _thresholds.get("block_threshold", 0.70)
    at = _thresholds.get("approve_threshold", 0.40)
    if score >= bt:
        return "BLOCK"
    if score >= at:
        return "REVIEW"
    return "APPROVE"


def _risk_level(score: float) -> str:
    ct = _thresholds.get("critical_threshold", 0.85)
    bt = _thresholds.get("block_threshold", 0.70)
    at = _thresholds.get("approve_threshold", 0.40)
    if score >= ct:
        return "Critical"
    if score >= bt:
        return "High"
    if score >= at:
        return "Medium"
    return "Low"


def _run_rules(txn: dict, account_status: str, velocity: int) -> tuple[float, list]:
    triggered = []
    score = 0.0
    rules = {r["rule_id"]: r for r in _rules_cache if r.get("is_active")}

    # R7 — Frozen account (always on, weight 1.0)
    if account_status == "Frozen":
        triggered.append("R7 — Frozen Account")
        return 1.0, triggered

    # R1 — High amount
    r1 = rules.get("R1", {})
    threshold = float(r1.get("threshold_value") or 100000)
    if txn.get("amount", 0) > threshold:
        triggered.append("R1 — High Amount")
        score += float(r1.get("weight", 0.25))

    # R2 — Odd hour
    r2 = rules.get("R2", {})
    hour = datetime.now().hour
    if 2 <= hour <= 5:
        triggered.append("R2 — Odd Hour (2–5 AM)")
        score += float(r2.get("weight", 0.15))

    # R3 — Balance drain
    r3 = rules.get("R3", {})
    if txn.get("oldbalanceOrg", 0) > 0 and txn.get("newbalanceOrig", 0) == 0:
        triggered.append("R3 — Balance Drain")
        score += float(r3.get("weight", 0.30))

    # R4 — Destination anomaly
    r4 = rules.get("R4", {})
    if txn.get("oldbalanceDest", 0) == 0 and txn.get("newbalanceDest", 0) == 0:
        triggered.append("R4 — Destination Anomaly")
        score += float(r4.get("weight", 0.20))

    # R5 — High-risk type
    r5 = rules.get("R5", {})
    if txn.get("type") in ("TRANSFER", "CASH_OUT"):
        triggered.append("R5 — High Risk Type")
        score += float(r5.get("weight", 0.10))

    # R6 — Velocity attack
    r6 = rules.get("R6", {})
    vel_threshold = int(float(r6.get("threshold_value") or 5))
    if velocity > vel_threshold:
        triggered.append(f"R6 — Velocity Attack ({velocity} txns in 10 min)")
        score += float(r6.get("weight", 0.35))

    return min(score, 1.0), triggered


def _compute_shap(scaled_row: np.ndarray) -> List[ShapFeature]:
    try:
        sv = _explainer.shap_values(scaled_row)
        vals = sv[0] if isinstance(sv, list) else sv[0]
        pairs = sorted(zip(_feature_cols, vals), key=lambda x: abs(x[1]), reverse=True)[:5]
        result = []
        for feat, val in pairs:
            abs_val = abs(float(val))
            impact = "high" if abs_val > 0.3 else "medium" if abs_val > 0.1 else "low"
            result.append(ShapFeature(feature=feat, value=round(float(val), 4), impact=impact))
        return result
    except Exception as e:
        logger.warning("SHAP failed: %s", e)
        return []


def _explain(txn: dict, score: float, triggered: list) -> list:
    reasons = []
    if score >= _thresholds.get("critical_threshold", 0.85):
        reasons.append("CRITICAL: Extremely high fraud probability — immediate review required")
    elif score >= _thresholds.get("block_threshold", 0.70):
        reasons.append("ML model flagged this transaction as high-risk")
    if txn.get("amount", 0) > 100_000:
        reasons.append(f"Unusually high amount: ${txn['amount']:,.2f}")
    if txn.get("oldbalanceOrg", 0) > 0 and txn.get("newbalanceOrig", 0) == 0:
        reasons.append("Sender balance completely drained to zero after transaction")
    if txn.get("oldbalanceDest", 0) == 0 and txn.get("newbalanceDest", 0) == 0:
        reasons.append("Destination balance unchanged despite receiving funds")
    if txn.get("type") in ("TRANSFER", "CASH_OUT"):
        reasons.append(f"Transaction type '{txn['type']}' is associated with higher fraud rates")
    for r in triggered:
        reasons.append(f"Rule triggered: {r}")
    return reasons or ["Transaction pattern is within normal parameters"]


def _run_prediction(txn: TransactionInput, source: str = "manual", actor: str = "system") -> dict:
    """Core prediction logic used by both /predict and /simulate."""
    # Check account status
    acct = DB.get_account(txn.orig_account) or {}
    acct_status = acct.get("status", "Active")
    velocity = DB.check_velocity(txn.orig_account)

    # Feature engineering
    raw_df = pd.DataFrame([{
        "step": txn.step, "type": txn.type.value,
        "amount": txn.amount,
        "oldbalanceOrg": txn.oldbalanceOrg, "newbalanceOrig": txn.newbalanceOrig,
        "oldbalanceDest": txn.oldbalanceDest, "newbalanceDest": txn.newbalanceDest,
    }])
    feat_df = prepare_features_for_model(raw_df, inference=True)
    scaled  = _scale(feat_df.values.astype(np.float64))

    # ML inference
    ml_prob = float(_model.predict_proba(scaled)[0, 1])

    # Rules engine
    txn_dict = {
        "amount": txn.amount, "type": txn.type.value,
        "oldbalanceOrg": txn.oldbalanceOrg, "newbalanceOrig": txn.newbalanceOrig,
        "oldbalanceDest": txn.oldbalanceDest, "newbalanceDest": txn.newbalanceDest,
    }
    rule_score, triggered = _run_rules(txn_dict, acct_status, velocity)

    # Hybrid score
    ml_w    = _thresholds.get("ml_weight", 0.60)
    rule_w  = _thresholds.get("rules_weight", 0.40)
    final   = round(ml_w * ml_prob + rule_w * rule_score, 6)
    decision = _decide(final)
    is_frozen_block = acct_status == "Frozen"

    # SHAP
    shap_exp = _compute_shap(scaled)

    # Reasons
    reasons = _explain(txn_dict, final, triggered)

    # Persist
    txn_id = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    ts = datetime.now(timezone.utc).isoformat()

    DB.insert_prediction(
        transaction_id=txn_id, source=source,
        tx_type=txn.type.value, amount=txn.amount,
        orig_account=txn.orig_account, dest_account=txn.dest_account,
        ml_probability=ml_prob, rule_score=rule_score,
        fraud_probability=final, decision=decision,
        triggered_rules=triggered,
    )

    # Auto-create case for REVIEW
    if decision == "REVIEW":
        try:
            DB.create_case(txn_id, priority="High" if final > 0.60 else "Medium")
        except Exception as e:
            logger.warning("Auto-case creation failed: %s", e)

    # Audit log
    DB.log_audit(actor, "PREDICTION", "transaction", txn_id, {
        "decision": decision, "score": final, "source": source,
    })

    logger.info("TXN %s | %s | score=%.3f | rules=%s", txn_id, decision, final, triggered)

    return {
        "transaction_id":   txn_id,
        "prediction":       "Fraud" if decision == "BLOCK" else "Not Fraud",
        "probability":      round(final * 100, 1),
        "ml_probability":   round(ml_prob * 100, 1),
        "rule_score":       round(rule_score * 100, 1),
        "risk_level":       _risk_level(final),
        "decision":         decision,
        "triggered_rules":  triggered,
        "reasons":          reasons,
        "shap_explanation": [s.model_dump() for s in shap_exp],
        "timestamp":        ts,
        "is_frozen_block":  is_frozen_block,
    }


# ══════════════════════════════════════════════════════════════════════════
# HTML PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", 302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = DB.get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        DB.log_audit(username, "LOGIN_FAILED", "user", username, {"reason": "bad credentials"})
        return templates.TemplateResponse("login.html", {
            "request": request, "error": "Invalid username or password"
        }, status_code=401)
    session_id = create_session(user["id"], user["username"], user["role"])
    DB.log_audit(username, "LOGIN_SUCCESS", "user", username, {})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(COOKIE_NAME, session_id, httponly=True, samesite="lax", max_age=28800)
    return response


@app.post("/logout")
def logout(request: Request):
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        user = get_current_user(request)
        delete_session(session_id)
        if user:
            DB.log_audit(user["username"], "LOGOUT", "user", user["username"], {})
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})


@app.get("/predict-page", response_class=HTMLResponse)
def predict_page(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("predict.html", {"request": request, "user": user})


@app.get("/history-page", response_class=HTMLResponse)
def history_page(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("history.html", {"request": request, "user": user})


@app.get("/cases", response_class=HTMLResponse)
def cases_page(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("cases.html", {"request": request, "user": user})


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("accounts.html", {"request": request, "user": user})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = require_admin(request)
    return templates.TemplateResponse("settings.html", {"request": request, "user": user})


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = require_admin(request)
    return templates.TemplateResponse("users.html", {"request": request, "user": user})


@app.get("/model-monitor", response_class=HTMLResponse)
def model_monitor_page(request: Request):
    user = require_admin(request)
    return templates.TemplateResponse("model_monitor.html", {"request": request, "user": user})


# ══════════════════════════════════════════════════════════════════════════
# REST API ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", model_loaded=_model is not None,
                          db_ready=True, model_version=_model_version)


@app.post("/predict")
def predict(txn: TransactionInput, request: Request):
    if _model is None:
        raise HTTPException(503, "Model not loaded.")
    user = get_current_user(request)
    actor = user["username"] if user else "api"
    return _run_prediction(txn, source="manual", actor=actor)


@app.post("/simulate")
def simulate(request: Request):
    if _model is None:
        raise HTTPException(503, "Model not loaded.")
    types   = ["TRANSFER", "CASH_OUT", "CASH_IN", "PAYMENT", "DEBIT"]
    weights = [0.30, 0.30, 0.15, 0.15, 0.10]
    tx_type = random.choices(types, weights=weights)[0]
    amount  = round(random.choices(
        [random.uniform(10, 1_000), random.uniform(1_000, 50_000), random.uniform(50_000, 500_000)],
        weights=[0.60, 0.30, 0.10]
    )[0], 2)
    old_org  = round(random.uniform(0, 500_000), 2)
    new_orig = round(max(0, old_org - amount), 2) if tx_type in ("TRANSFER","CASH_OUT") else old_org
    old_dest = round(random.uniform(0, 200_000), 2)
    new_dest = round(old_dest + amount, 2) if tx_type in ("TRANSFER","CASH_IN") else old_dest
    # Inject fraud ~10%
    if random.random() < 0.10:
        amount   = round(random.uniform(80_000, 500_000), 2)
        old_org  = amount; new_orig = 0.0; old_dest = 0.0; new_dest = 0.0
        tx_type  = random.choice(["TRANSFER","CASH_OUT"])
    acc_num = f"ACC-{random.randint(1000,9999)}"
    from api.schemas import TransactionType
    txn = TransactionInput(
        step=random.randint(1, 744), type=TransactionType(tx_type),
        amount=amount, orig_account=acc_num,
        dest_account=f"ACC-{random.randint(1000,9999)}",
        oldbalanceOrg=old_org, newbalanceOrig=new_orig,
        oldbalanceDest=old_dest, newbalanceDest=new_dest,
    )
    return _run_prediction(txn, source="simulated", actor="simulator")


@app.get("/transactions/recent")
def recent_transactions(
    request: Request,
    limit:    int = Query(50, ge=1, le=200),
    decision: Optional[str] = None,
    source:   Optional[str] = None,
    date_from:Optional[str] = None,
    date_to:  Optional[str] = None,
    search:   Optional[str] = None,
):
    user = require_auth(request)
    rows = DB.fetch_recent_predictions(
        limit=limit, user_role=user["role"], username=user["username"],
        decision_filter=decision, source_filter=source,
        date_from=date_from, date_to=date_to, search=search,
    )
    return {"count": len(rows), "predictions": rows}


@app.get("/stats")
def stats(request: Request):
    require_auth(request)
    return DB.get_stats()


# ── Cases ─────────────────────────────────────────────────────────────────

@app.get("/api/cases")
def get_cases(
    request: Request,
    status:   Optional[str] = None,
    priority: Optional[str] = None,
    assigned: Optional[str] = None,
    limit:    int = Query(100, ge=1, le=500),
):
    require_auth(request)
    return DB.get_cases(status_filter=status, priority_filter=priority,
                        assigned_to=assigned, limit=limit)


@app.post("/api/cases")
def create_case(body: CaseCreate, request: Request):
    user = require_auth(request)
    case = DB.create_case(body.transaction_id, body.priority)
    DB.log_audit(user["username"], "CASE_CREATED", "case", body.transaction_id, {"priority": body.priority})
    return case


@app.get("/api/cases/{case_id}")
def get_case(case_id: int, request: Request):
    require_auth(request)
    case = DB.get_case_detail(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@app.put("/api/cases/{case_id}")
def update_case(case_id: int, body: CaseUpdate, request: Request):
    user = require_auth(request)
    updates = body.model_dump(exclude_none=True)
    note = updates.pop("note", None)
    if updates:
        DB.update_case(case_id, updates, actor=user["username"])
    if note:
        DB.add_case_note(case_id, user["username"], note)
    DB.log_audit(user["username"], "CASE_UPDATED", "case", str(case_id), updates)
    return {"ok": True}


# ── Accounts ──────────────────────────────────────────────────────────────

@app.get("/api/accounts")
def get_accounts(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    require_auth(request)
    return DB.get_accounts(status_filter=status, search=search)


@app.get("/api/accounts/{account_id}")
def get_account(account_id: str, request: Request):
    require_auth(request)
    acct = DB.get_account(account_id)
    if not acct:
        return {"account_id": account_id, "status": "Active", "total_transactions": 0,
                "total_fraud_flags": 0, "avg_risk_score": 0, "recent_transactions": []}
    return acct


@app.get("/api/accounts/{account_id}/velocity")
def account_velocity(account_id: str, request: Request):
    require_auth(request)
    count = DB.check_velocity(account_id)
    return {"account_id": account_id, "transactions_last_10min": count, "is_high_velocity": count > 5}


@app.post("/api/accounts/{account_id}/freeze")
def freeze_account(account_id: str, body: AccountFreeze, request: Request):
    user = require_auth(request)
    DB.freeze_account(account_id, user["username"], body.reason)
    DB.log_audit(user["username"], "ACCOUNT_FROZEN", "account", account_id, {"reason": body.reason})
    return {"ok": True, "account_id": account_id, "status": "Frozen"}


@app.post("/api/accounts/{account_id}/unfreeze")
def unfreeze_account(account_id: str, request: Request):
    user = require_admin(request)
    DB.unfreeze_account(account_id, user["username"])
    DB.log_audit(user["username"], "ACCOUNT_UNFROZEN", "account", account_id, {})
    return {"ok": True, "account_id": account_id, "status": "Active"}


# ── Override ──────────────────────────────────────────────────────────────

@app.post("/api/transactions/{txn_id}/override")
def override_transaction(txn_id: str, body: OverrideRequest, request: Request):
    user = require_auth(request)
    DB.override_decision(txn_id, "APPROVE", user["username"], body.reason)
    DB.log_audit(user["username"], "DECISION_OVERRIDDEN", "transaction", txn_id,
                 {"new_decision": "APPROVE", "reason": body.reason})
    return {"ok": True, "transaction_id": txn_id, "new_decision": "APPROVE"}


# ── Rules ─────────────────────────────────────────────────────────────────

@app.get("/api/rules")
def get_rules(request: Request):
    require_admin(request)
    return DB.get_rules()


@app.put("/api/rules")
def update_rules(rules: List[RuleUpdate], request: Request):
    user = require_admin(request)
    DB.update_rules([r.model_dump() for r in rules], modified_by=user["username"])
    _reload_rules()
    DB.log_audit(user["username"], "RULES_UPDATED", "rule_config", "all", {})
    return {"ok": True}


# ── Settings / Thresholds ─────────────────────────────────────────────────

@app.get("/api/settings/thresholds")
def get_thresholds(request: Request):
    require_admin(request)
    return DB.get_thresholds()


@app.put("/api/settings/thresholds")
def update_thresholds(body: ThresholdUpdate, request: Request):
    user = require_admin(request)
    DB.update_thresholds(body.model_dump(), modified_by=user["username"])
    _reload_thresholds()
    DB.log_audit(user["username"], "THRESHOLDS_UPDATED", "settings", "thresholds", body.model_dump())
    return {"ok": True}


# ── Users ─────────────────────────────────────────────────────────────────

@app.get("/api/users")
def get_users(request: Request):
    require_admin(request)
    return DB.get_users()


@app.post("/api/users")
def create_user(body: UserCreate, request: Request):
    from api.auth import hash_password
    user = require_admin(request)
    temp_pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    pw_hash = hash_password(temp_pw)
    uid = DB.create_user(body.username, pw_hash, "analyst", body.full_name, body.email, user["username"])
    DB.log_audit(user["username"], "USER_CREATED", "user", body.username, {"role": "analyst"})
    return {"ok": True, "user_id": uid, "temp_password": temp_pw}


@app.put("/api/users/{user_id}")
def update_user(user_id: int, request: Request):
    require_admin(request)
    DB.deactivate_user(user_id)
    return {"ok": True}


# ── Audit log ─────────────────────────────────────────────────────────────

@app.get("/api/audit-log")
def audit_log(request: Request, limit: int = Query(200, ge=1, le=1000)):
    require_admin(request)
    return DB.get_audit_log(limit=limit)


# ── Model stats ───────────────────────────────────────────────────────────

@app.get("/api/model-stats")
def model_stats(request: Request):
    require_admin(request)
    stats = DB.get_model_stats()
    # Add feature importances from XGBoost
    if _model is not None:
        try:
            scores = _model.get_booster().get_fscore()
            total  = sum(scores.values()) or 1
            stats["feature_importances"] = sorted(
                [{"feature": k, "importance": round(v / total, 4)} for k, v in scores.items()],
                key=lambda x: x["importance"], reverse=True
            )[:10]
        except Exception:
            stats["feature_importances"] = []
    stats["model_version"] = _model_version
    return stats
