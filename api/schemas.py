"""
api/schemas.py — Pydantic v2 models for PayRadar
"""
from __future__ import annotations
from enum import Enum
from typing import List, Optional, Any
from pydantic import BaseModel, Field


class TransactionType(str, Enum):
    CASH_IN   = "CASH_IN"
    CASH_OUT  = "CASH_OUT"
    DEBIT     = "DEBIT"
    PAYMENT   = "PAYMENT"
    TRANSFER  = "TRANSFER"


class Decision(str, Enum):
    APPROVE = "APPROVE"
    REVIEW  = "REVIEW"
    BLOCK   = "BLOCK"


class TransactionInput(BaseModel):
    step:           int   = Field(..., ge=1)
    type:           TransactionType
    amount:         float = Field(..., ge=0)
    orig_account:   str   = Field(default="ACC-UNKNOWN")
    dest_account:   str   = Field(default="ACC-UNKNOWN")
    oldbalanceOrg:  float = Field(..., ge=0)
    newbalanceOrig: float = Field(..., ge=0)
    oldbalanceDest: float = Field(..., ge=0)
    newbalanceDest: float = Field(..., ge=0)

    model_config = {"json_schema_extra": {"example": {
        "step": 1, "type": "TRANSFER", "amount": 150000,
        "orig_account": "ACC-001", "dest_account": "ACC-002",
        "oldbalanceOrg": 150000, "newbalanceOrig": 0,
        "oldbalanceDest": 0, "newbalanceDest": 0,
    }}}


class ShapFeature(BaseModel):
    feature: str
    value:   float
    impact:  str   # "high" | "medium" | "low"


class PredictionResponse(BaseModel):
    transaction_id:   str
    prediction:       str        # "Fraud" | "Not Fraud"
    probability:      float      # 0-100
    ml_probability:   float
    rule_score:       float
    risk_level:       str        # Low | Medium | High | Critical
    decision:         str
    triggered_rules:  List[str]
    reasons:          List[str]
    shap_explanation: List[ShapFeature]
    timestamp:        str
    is_frozen_block:  bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class OverrideRequest(BaseModel):
    reason: str = Field(..., min_length=20)


class AccountFreeze(BaseModel):
    reason: str = Field(..., min_length=10)


class CaseCreate(BaseModel):
    transaction_id: str
    priority:       str = "Medium"


class CaseUpdate(BaseModel):
    status:      Optional[str] = None
    assigned_to: Optional[str] = None
    priority:    Optional[str] = None
    resolution:  Optional[str] = None
    note:        Optional[str] = None


class RuleUpdate(BaseModel):
    rule_id:         str
    weight:          float
    threshold_value: Optional[float] = None
    is_active:       bool = True


class UserCreate(BaseModel):
    username:  str
    full_name: str
    email:     str
    role:      str = "analyst"


class HealthResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    status:        str
    model_loaded:  bool
    db_ready:      bool
    model_version: str


class ThresholdUpdate(BaseModel):
    approve_threshold:  float
    block_threshold:    float
    critical_threshold: float
    ml_weight:          float
    rules_weight:       float
