"""
src/train.py
============
Training script — zero sklearn, zero imbalanced-learn.
Uses only: numpy, pandas, xgboost, joblib.

Manual implementations:
  - Stratified train/val/test split
  - StandardScaler
  - SMOTE oversampling
  - Precision, Recall, F1, PR-AUC, Confusion matrix

Run from repo root:
    python src/train.py
"""

import json
import os
import sys
import warnings
import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import (
    validate_input_columns,
    prepare_features_for_model,
    get_model_feature_columns,
)

from xgboost import XGBClassifier

# ── Paths ─────────────────────────────────────────────────────────────────
DATA_PATH   = os.path.join("data", "raw", "transactions.csv")
MODELS_DIR  = "models"
MODEL_PATH  = os.path.join(MODELS_DIR, "fraud_model.pkl")
SCALER_PATH = os.path.join(MODELS_DIR, "scaler.pkl")
META_PATH   = os.path.join(MODELS_DIR, "model_meta.json")
FEAT_PATH   = os.path.join(MODELS_DIR, "feature_columns.json")

RANDOM_STATE      = 42
APPROVE_THRESHOLD = 0.40
BLOCK_THRESHOLD   = 0.70

np.random.seed(RANDOM_STATE)


# ══════════════════════════════════════════════════════════════════════════
# 1. MANUAL STANDARD SCALER (stored as plain dict — no custom class)
# ══════════════════════════════════════════════════════════════════════════

def scaler_fit(X: np.ndarray) -> dict:
    """Fit scaler on X, return as plain dict (safe to pickle)."""
    mean = X.mean(axis=0)
    std  = X.std(axis=0)
    std[std == 0] = 1.0
    return {"mean": mean, "std": std}


def scaler_transform(scaler: dict, X: np.ndarray) -> np.ndarray:
    """Apply z-score normalisation using fitted scaler dict."""
    return (X - scaler["mean"]) / scaler["std"]
# ══════════════════════════════════════════════════════════════════════════

def stratified_split(X: np.ndarray, y: np.ndarray,
                     val_ratio: float = 0.15,
                     test_ratio: float = 0.15):
    """
    Stratified 70/15/15 split without sklearn.
    Keeps the fraud rate consistent across all three splits.
    """
    fraud_idx = np.where(y == 1)[0]
    legit_idx = np.where(y == 0)[0]

    np.random.shuffle(fraud_idx)
    np.random.shuffle(legit_idx)

    def _split_indices(idx):
        n      = len(idx)
        n_val  = int(n * val_ratio)
        n_test = int(n * test_ratio)
        return idx[n_val+n_test:], idx[:n_val], idx[n_val:n_val+n_test]

    f_train, f_val, f_test = _split_indices(fraud_idx)
    l_train, l_val, l_test = _split_indices(legit_idx)

    train_idx = np.concatenate([f_train, l_train])
    val_idx   = np.concatenate([f_val,   l_val])
    test_idx  = np.concatenate([f_test,  l_test])

    np.random.shuffle(train_idx)
    np.random.shuffle(val_idx)
    np.random.shuffle(test_idx)

    return (X[train_idx], X[val_idx], X[test_idx],
            y[train_idx], y[val_idx], y[test_idx])


# ══════════════════════════════════════════════════════════════════════════
# 3. MANUAL SMOTE
# ══════════════════════════════════════════════════════════════════════════

def manual_smote(X: np.ndarray, y: np.ndarray,
                 k: int = 5) -> tuple:
    """
    Synthetic Minority Over-sampling Technique (SMOTE).
    For each minority sample, pick k nearest neighbours and
    interpolate a synthetic point between them.

    Applied to training data ONLY — never to val or test.
    """
    minority_X = X[y == 1]
    majority_X = X[y == 0]
    n_to_gen   = len(majority_X) - len(minority_X)

    if n_to_gen <= 0:
        return X, y

    print(f"  SMOTE: generating {n_to_gen:,} synthetic fraud samples …")
    synthetic = []

    for i in range(n_to_gen):
        # Pick a random minority sample
        idx    = np.random.randint(0, len(minority_X))
        sample = minority_X[idx]

        # Find k nearest neighbours (Euclidean distance)
        dists  = np.linalg.norm(minority_X - sample, axis=1)
        dists[idx] = np.inf                          # exclude self
        nn_idx = np.argsort(dists)[:k]
        nn     = minority_X[np.random.choice(nn_idx)]

        # Interpolate
        alpha = np.random.random()
        synthetic.append(sample + alpha * (nn - sample))

    synthetic = np.array(synthetic)
    X_res = np.vstack([X, synthetic])
    y_res = np.concatenate([y, np.ones(len(synthetic), dtype=int)])

    # Shuffle
    perm  = np.random.permutation(len(X_res))
    return X_res[perm], y_res[perm]


# ══════════════════════════════════════════════════════════════════════════
# 4. MANUAL METRICS
# ══════════════════════════════════════════════════════════════════════════

def confusion_matrix_manual(y_true, y_pred):
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())
    return tp, fp, fn, tn


def precision_manual(y_true, y_pred):
    tp, fp, fn, tn = confusion_matrix_manual(y_true, y_pred)
    return tp / (tp + fp) if (tp + fp) > 0 else 0.0


def recall_manual(y_true, y_pred):
    tp, fp, fn, tn = confusion_matrix_manual(y_true, y_pred)
    return tp / (tp + fn) if (tp + fn) > 0 else 0.0


def f1_manual(y_true, y_pred):
    p = precision_manual(y_true, y_pred)
    r = recall_manual(y_true, y_pred)
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def pr_auc_manual(y_true, probs):
    """
    Compute PR-AUC using the trapezoidal rule.
    Sweeps thresholds from 0 to 1 in 200 steps.
    """
    thresholds = np.linspace(0, 1, 200)
    precisions, recalls = [], []

    for t in thresholds:
        preds = (probs >= t).astype(int)
        precisions.append(precision_manual(y_true, preds))
        recalls.append(recall_manual(y_true, preds))

    # Sort by recall for trapezoidal integration
    pairs = sorted(zip(recalls, precisions))
    r_arr = np.array([p[0] for p in pairs])
    p_arr = np.array([p[1] for p in pairs])
    return float(np.trapz(p_arr, r_arr))


# ══════════════════════════════════════════════════════════════════════════
# 5. TRAINING PIPELINE
# ══════════════════════════════════════════════════════════════════════════

def load_data():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(
            f"Dataset not found at '{DATA_PATH}'. "
            "Run: python data/convert_dataset.py"
        )
    print(f"Loading {DATA_PATH} …")
    df = pd.read_csv(DATA_PATH)
    print(f"  {len(df):,} rows loaded.")
    return df


def build_features(df):
    validate_input_columns(df, inference=False)
    X = prepare_features_for_model(df, inference=False)
    y = df["isFraud"].values.astype(int)
    print(f"  Features: {X.shape[1]} columns | Fraud rate: {y.mean():.4%}")
    return X.values.astype(np.float64), y


def train_variant(name, X_train, y_train, scale_pos_weight):
    print(f"  Training {name} …")
    model = XGBClassifier(
        objective        = "binary:logistic",
        eval_metric      = "aucpr",
        n_estimators     = 400,
        learning_rate    = 0.05,
        max_depth        = 6,
        min_child_weight = 3,
        subsample        = 0.8,
        colsample_bytree = 0.8,
        scale_pos_weight = scale_pos_weight,
        random_state     = RANDOM_STATE,
        n_jobs           = -1,
        verbosity        = 0,
    )
    model.fit(X_train, y_train)
    return model


def evaluate(model, X, y, label):
    probs  = model.predict_proba(X)[:, 1]
    auc    = pr_auc_manual(y, probs)
    print(f"    {label} PR-AUC: {auc:.4f}")
    return auc, probs


def threshold_report(probs, y, label):
    print(f"\n  === Threshold Report ({label}) ===")
    for t in [APPROVE_THRESHOLD, BLOCK_THRESHOLD]:
        preds = (probs >= t).astype(int)
        p = precision_manual(y, preds)
        r = recall_manual(y, preds)
        f = f1_manual(y, preds)
        print(f"  t={t:.2f} → Precision:{p:.4f}  Recall:{r:.4f}  F1:{f:.4f}")


def save_artifacts(model, scaler, metrics):
    os.makedirs(MODELS_DIR, exist_ok=True)
    joblib.dump(model,  MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)
    print(f"  Saved model  → {MODEL_PATH}")
    print(f"  Saved scaler → {SCALER_PATH}")

    meta = {
        "model_version":    "1.0.0",
        "approve_threshold": APPROVE_THRESHOLD,
        "block_threshold":   BLOCK_THRESHOLD,
        "primary_metric":   "PR-AUC",
        "metrics":          metrics,
        "note":             "No sklearn used. Pure numpy metrics.",
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    with open(FEAT_PATH, "w") as f:
        json.dump({"feature_columns": get_model_feature_columns()}, f, indent=2)
    print(f"  Saved meta   → {META_PATH}")


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*55)
    print("  FRAUD DETECTION — TRAINING (no sklearn / imblearn)")
    print("="*55)

    # 1. Load
    df = load_data()

    # 2. Features
    X, y = build_features(df)

    # 3. Stratified split (manual)
    print("\nSplitting dataset 70/15/15 (stratified, manual) …")
    X_train, X_val, X_test, y_train, y_val, y_test = stratified_split(X, y)
    print(f"  Train:{len(X_train):,}  Val:{len(X_val):,}  Test:{len(X_test):,}")
    print(f"  Train fraud rate: {y_train.mean():.4%}")

    # 4. Scale (manual dict-based — no custom class)
    print("\nFitting scaler on training data …")
    scaler  = scaler_fit(X_train)
    X_tr_sc = scaler_transform(scaler, X_train)
    X_va_sc = scaler_transform(scaler, X_val)
    X_te_sc = scaler_transform(scaler, X_test)

    # 5. SMOTE on training only (manual)
    X_sm, y_sm = manual_smote(X_tr_sc, y_train)

    # 6. Train 3 variants
    spw = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    print(f"\nscale_pos_weight = {spw:.1f}")
    print("\nTraining candidate models …")
    m1 = train_variant("Variant 1 — Weighted XGBoost (no SMOTE)", X_tr_sc, y_train, spw)
    m2 = train_variant("Variant 2 — SMOTE XGBoost (spw=1)",       X_sm,    y_sm,    1)
    m3 = train_variant("Variant 3 — SMOTE + Weighted XGBoost",    X_sm,    y_sm,    spw)

    # 7. Compare validation PR-AUC
    print("\nValidation PR-AUC comparison …")
    s1, _ = evaluate(m1, X_va_sc, y_val, "Variant 1")
    s2, _ = evaluate(m2, X_va_sc, y_val, "Variant 2")
    s3, _ = evaluate(m3, X_va_sc, y_val, "Variant 3")

    scores = {"Variant 1": (s1, m1), "Variant 2": (s2, m2), "Variant 3": (s3, m3)}
    best_name, (best_score, best_model) = max(scores.items(), key=lambda x: x[1][0])
    print(f"\n  Best: {best_name}  PR-AUC={best_score:.4f}")

    # 8. Threshold report on validation
    _, val_probs = evaluate(best_model, X_va_sc, y_val, "Best (val)")
    threshold_report(val_probs, y_val, "Validation")

    # 9. Final test evaluation
    test_auc, test_probs = evaluate(best_model, X_te_sc, y_test, "Best (test)")
    threshold_report(test_probs, y_test, "Test")

    preds_040 = (test_probs >= APPROVE_THRESHOLD).astype(int)
    preds_070 = (test_probs >= BLOCK_THRESHOLD).astype(int)

    metrics = {
        "pr_auc":           round(test_auc, 4),
        "best_variant":     best_name,
        "precision_at_040": round(precision_manual(y_test, preds_040), 4),
        "recall_at_040":    round(recall_manual(y_test, preds_040), 4),
        "f1_at_040":        round(f1_manual(y_test, preds_040), 4),
        "precision_at_070": round(precision_manual(y_test, preds_070), 4),
        "recall_at_070":    round(recall_manual(y_test, preds_070), 4),
        "f1_at_070":        round(f1_manual(y_test, preds_070), 4),
    }

    print("\n" + "="*55)
    print(f"  TEST PR-AUC : {metrics['pr_auc']}")
    print(f"  Precision@0.40 : {metrics['precision_at_040']}")
    print(f"  Recall@0.40    : {metrics['recall_at_040']}")
    print("="*55)

    # 10. Save
    print("\nSaving artifacts …")
    save_artifacts(best_model, scaler, metrics)
    print("\nTraining complete.")


if __name__ == "__main__":
    main()
