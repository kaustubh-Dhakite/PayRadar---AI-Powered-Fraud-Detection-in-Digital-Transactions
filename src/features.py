"""
src/features.py
===============
Single source of truth for all feature engineering logic.
Training, API inference, and dashboard inference all import from here.
Never duplicate these formulas elsewhere.
"""

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_RAW_COLUMNS = [
    "step",
    "type",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
]

# Columns required at inference time (no isFraud label needed)
INFERENCE_COLUMNS = [
    "step",
    "type",
    "amount",
    "oldbalanceOrg",
    "newbalanceOrig",
    "oldbalanceDest",
    "newbalanceDest",
]

# Transaction type encoding — deterministic, order-stable
TYPE_ENCODING = {
    "CASH_IN": 0,
    "CASH-IN": 0,
    "CASH_OUT": 1,
    "CASH-OUT": 1,
    "DEBIT": 2,
    "PAYMENT": 3,
    "TRANSFER": 4,
}

# Safe division epsilon
EPS = 1.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_input_columns(df: pd.DataFrame, inference: bool = False) -> None:
    """
    Raise ValueError if required columns are missing from df.

    Parameters
    ----------
    df : pd.DataFrame
        Input dataframe to validate.
    inference : bool
        If True, validate against INFERENCE_COLUMNS (no isFraud needed).
        If False, validate against REQUIRED_RAW_COLUMNS (training mode).
    """
    required = INFERENCE_COLUMNS if inference else REQUIRED_RAW_COLUMNS
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Expected: {required}"
        )


# ---------------------------------------------------------------------------
# Engineered features
# ---------------------------------------------------------------------------

def add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all 7 engineered features and append them to df.
    Returns a new DataFrame — does not modify the input in place.

    Features
    --------
    1. orig_balance_delta      — how much the sender balance changed
    2. dest_balance_delta      — how much the receiver balance changed
    3. orig_expected_gap       — sender-side consistency check
    4. dest_expected_gap       — destination-side consistency check
    5. amount_to_orig_balance_ratio — transaction size relative to sender balance
    6. amount_to_dest_balance_ratio — transaction size relative to destination balance
    7. zero_balance_anomaly    — suspicious balance-reset behaviour flag
    """
    df = df.copy()

    # 1. How much the sender's balance changed
    df["orig_balance_delta"] = df["oldbalanceOrg"] - df["newbalanceOrig"]

    # 2. How much the receiver's balance changed
    df["dest_balance_delta"] = df["newbalanceDest"] - df["oldbalanceDest"]

    # 3. Sender-side consistency: if legitimate, (oldbalanceOrg - amount) ≈ newbalanceOrig
    df["orig_expected_gap"] = (
        (df["oldbalanceOrg"] - df["amount"]) - df["newbalanceOrig"]
    ).abs()

    # 4. Destination-side consistency: if legitimate, (oldbalanceDest + amount) ≈ newbalanceDest
    df["dest_expected_gap"] = (
        (df["oldbalanceDest"] + df["amount"]) - df["newbalanceDest"]
    ).abs()

    # 5. Transaction size relative to sender balance (safe division)
    df["amount_to_orig_balance_ratio"] = df["amount"] / df["oldbalanceOrg"].clip(lower=EPS)

    # 6. Transaction size relative to destination balance (safe division)
    df["amount_to_dest_balance_ratio"] = df["amount"] / df["oldbalanceDest"].clip(lower=EPS)

    # 7. Suspicious balance-reset: sender drained to zero OR destination never moved
    df["zero_balance_anomaly"] = (
        (df["amount"] > 0)
        & (
            ((df["oldbalanceOrg"] > 0) & (df["newbalanceOrig"] == 0))
            | ((df["oldbalanceDest"] == 0) & (df["newbalanceDest"] == 0))
        )
    ).astype(int)

    return df


# ---------------------------------------------------------------------------
# Type encoding
# ---------------------------------------------------------------------------

def encode_transaction_type(df: pd.DataFrame) -> pd.DataFrame:
    """
    Encode the 'type' column to an integer using TYPE_ENCODING.
    Normalises hyphen/underscore variants (e.g. CASH-IN → CASH_IN).
    Returns a new DataFrame.
    """
    df = df.copy()
    # Normalise: upper-case and replace hyphens
    df["type"] = df["type"].str.upper().str.replace("-", "_", regex=False)
    df["type"] = df["type"].map(TYPE_ENCODING)
    if df["type"].isna().any():
        raise ValueError(
            "Unknown transaction type found. "
            f"Allowed types: {list(TYPE_ENCODING.keys())}"
        )
    return df


# ---------------------------------------------------------------------------
# Feature column order
# ---------------------------------------------------------------------------

def get_model_feature_columns() -> list:
    """
    Return the ordered list of feature columns the model expects.
    This order must match what was used during training.
    """
    return [
        # Raw numeric
        "step",
        "amount",
        "oldbalanceOrg",
        "newbalanceOrig",
        "oldbalanceDest",
        "newbalanceDest",
        # Raw categorical (encoded)
        "type",
        # Engineered
        "orig_balance_delta",
        "dest_balance_delta",
        "orig_expected_gap",
        "dest_expected_gap",
        "amount_to_orig_balance_ratio",
        "amount_to_dest_balance_ratio",
        "zero_balance_anomaly",
    ]


# ---------------------------------------------------------------------------
# Full pipeline: raw → model-ready feature matrix
# ---------------------------------------------------------------------------

def prepare_features_for_model(df: pd.DataFrame, inference: bool = False) -> pd.DataFrame:
    """
    Full feature preparation pipeline:
      1. Validate columns
      2. Encode transaction type
      3. Add engineered features
      4. Select and order model feature columns

    Parameters
    ----------
    df : pd.DataFrame
        Raw transaction dataframe.
    inference : bool
        Set True when called from the API (no isFraud column required).

    Returns
    -------
    pd.DataFrame
        Feature matrix ready for the model, columns in the correct order.
    """
    validate_input_columns(df, inference=inference)
    df = encode_transaction_type(df)
    df = add_engineered_features(df)
    feature_cols = get_model_feature_columns()
    return df[feature_cols]
