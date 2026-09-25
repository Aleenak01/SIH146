"""
Score fusion service: combined score, priority rank/level and the explanation of each investigative lead.

The combined score itself is the existing ml/result_fusion.py formula (kept as is):
    combined_score = FORENSIC_WEIGHT * (rule_count / total_rules) + ML_WEIGHT * anomaly_score
with FORENSIC_WEIGHT = 0.4 and ML_WEIGHT = 0.6. Those weights are a prototype configuration, NOT statistically
validated, and every output that carries them says so.

Added here (labelled prototype settings, not validated either):
  * priority_rank   1 = highest combined_score among the scored wallets (ties: wallet address).
  * priority_level  bands of that rank: top 5% High, next 15% Medium, the rest Low.
  * lead criterion  a wallet is an investigative lead if the Isolation Forest flags it (Anomalous) OR its priority
                    level is High (this keeps a wallet with strong rule evidence that the model missed; on the project
                    dataset that is 41 flagged + 1 extra = 42 leads. Including the Medium band would add 40 weaker,
                    rule-only wallets and double the review queue).
A lead is only a lead: no case exists until an investigator creates one.
"""

from __future__ import annotations

import math
from typing import Any

from .rules_meta import rule_label

FUSION_LABEL = "Prototype fusion weighting - not statistically validated."
PRIORITY_BAND_LABEL = "Prototype priority bands (top 5% High, next 15% Medium, rest Low) - not statistically validated."
HIGH_SHARE = 0.05
MEDIUM_SHARE = 0.20        # cumulative: High + Medium = top 20%


def priority_level(rank: int, n: int) -> str:
    if rank <= math.ceil(HIGH_SHARE * n):
        return "High"
    if rank <= math.ceil(MEDIUM_SHARE * n):
        return "Medium"
    return "Low"


def rank_wallets(fusion_df) -> dict[str, tuple[int, str]]:
    """{wallet: (priority_rank, priority_level)} from the fusion table."""
    ordered = fusion_df.sort_values(["combined_score", "wallet_address"], ascending=[False, True], kind="stable")
    n = len(ordered)
    return {w: (i, priority_level(i, n)) for i, w in enumerate(ordered["wallet_address"], start=1)}


def is_lead(ml_prediction: str, level: str) -> bool:
    return ml_prediction == "Anomalous" or level == "High"


def build_reasons(*, ml_score: float, ml_prediction: str, rule_ids: list[str], total_rules: int, combined: float,
                  rank: int, n: int, level: str, ml_weight: float, forensic_weight: float) -> list[str]:
    reasons = []
    if ml_prediction == "Anomalous":
        reasons.append(f"Anomalous behavior detected: the Isolation Forest flags this wallet (score {ml_score:.3f}).")
    else:
        reasons.append(f"Not flagged by the Isolation Forest (score {ml_score:.3f}); surfaced by its combined result.")
    if rule_ids:
        labels = ", ".join(rule_label(r) for r in rule_ids)
        reasons.append(f"{len(rule_ids)} of {total_rules} forensic rules triggered: {labels}.")
    else:
        reasons.append("No forensic rule was triggered.")
    reasons.append(
        f"Ranked #{rank} of {n} by combined score {combined:.3f} ({level} priority band). "
        f"{ml_weight:.0%} ML / {forensic_weight:.0%} forensic. {FUSION_LABEL}"
    )
    reasons.append("Flagged for investigation. Requires investigator review; this is not evidence of criminal activity.")
    return reasons


def contributing_evidence(*, row: Any, findings: list[dict[str, Any]], rank: int, n: int, level: str, reasons: list[str]) -> dict[str, Any]:
    """The structured evidence stored with a lead (row = one row of the fusion table)."""
    return {
        "ml": {"model": "Isolation Forest (unsupervised)", "anomaly_score": float(row["ml_anomaly_score"]), "prediction": row["ml_anomaly_prediction"]},
        "forensic": {
            "rule_count": int(row["forensic_rule_count"]), "evidence_level": row["forensic_evidence_level"],
            "rules": [{"rule_id": f["rule_id"], "rule_name": rule_label(f["rule_id"]), "feature": f["feature"],
                       "value": f["value"], "threshold": f["threshold"], "evidence": f["evidence"]} for f in findings],
        },
        "fusion": {
            "forensic_score": float(row["forensic_score"]), "ml_score": float(row["ml_anomaly_score"]),
            "combined_score": float(row["combined_score"]),
            "forensic_weight": float(row["forensic_weight"]), "ml_weight": float(row["ml_weight"]),
            "label": FUSION_LABEL,
        },
        "priority": {"rank": rank, "of": n, "level": level, "label": PRIORITY_BAND_LABEL},
        "reasons": reasons,
    }
