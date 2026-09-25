"""Score fusion, priority bands, lead criterion and explanations."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.analysis import fusion
from backend.analysis.ml_bridge import load_ml
from backend.analysis.rules_meta import RULE_LABELS, rule_label


@pytest.mark.parametrize("n,rank,expected", [
    (410, 1, "High"), (410, 21, "High"), (410, 22, "Medium"), (410, 82, "Medium"), (410, 83, "Low"), (410, 410, "Low"),
    (20, 1, "High"), (20, 2, "Medium"), (20, 4, "Medium"), (20, 5, "Low"),
    (1, 1, "High"),
])
def test_priority_bands(n, rank, expected):
    assert fusion.priority_level(rank, n) == expected


def test_ranking_orders_by_combined_score_then_wallet_address():
    df = pd.DataFrame({"wallet_address": ["b", "c", "a", "d"], "combined_score": [0.5, 0.9, 0.5, 0.1]})
    ranks = fusion.rank_wallets(df)
    assert [ranks[w][0] for w in ("c", "a", "b", "d")] == [1, 2, 3, 4]        # tie between a and b broken by address
    assert ranks["c"][1] == "High" and ranks["d"][1] == "Low"


def test_lead_criterion():
    assert fusion.is_lead("Anomalous", "Low")          # flagged by the model
    assert fusion.is_lead("Normal", "High")            # strong combined evidence the model alone missed
    assert not fusion.is_lead("Normal", "Medium")
    assert not fusion.is_lead("Normal", "Low")


def test_combined_score_is_the_existing_formula():
    """The fusion arithmetic is ml/result_fusion.py's own: 0.4 * rule_count/11 + 0.6 * anomaly_score."""
    _, _, _, rf = load_ml()
    assert (rf.FORENSIC_WEIGHT, rf.ML_WEIGHT, rf.TOTAL_FORENSIC_RULES) == (0.4, 0.6, 11)
    forensic_row = {"rule_count": 6, "evidence_level": "Multiple strong behavioural indicators", "triggered_rules": "fan_in;fan_out", "explanation": "x"}
    ml_row = {"anomaly_score": 0.75, "anomaly_prediction": "Anomalous"}
    r = rf.fuse_wallet("w", forensic_row, ml_row)
    assert r["combined_score"] == pytest.approx(0.4 * 6 / 11 + 0.6 * 0.75, abs=1e-6)
    assert r["valid"]


def test_reasons_are_investigative_not_accusatory_and_carry_the_prototype_label():
    reasons = fusion.build_reasons(ml_score=0.758, ml_prediction="Anomalous", rule_ids=["fan_in", "fan_out"], total_rules=11,
                                   combined=0.673, rank=1, n=410, level="High", ml_weight=0.6, forensic_weight=0.4)
    text = " ".join(reasons)
    assert "Anomalous behavior detected" in text and "2 of 11 forensic rules triggered: High fan-in, High fan-out" in text
    assert "Ranked #1 of 410" in text and "not statistically validated" in text
    assert "Requires investigator review" in text and "not evidence of criminal activity" in text
    assert "guilty" not in text.lower() and "criminal network" not in text.lower()

    quiet = fusion.build_reasons(ml_score=0.4, ml_prediction="Normal", rule_ids=[], total_rules=11, combined=0.2, rank=300, n=410,
                                 level="Low", ml_weight=0.6, forensic_weight=0.4)
    assert "Not flagged by the Isolation Forest" in quiet[0] and "No forensic rule was triggered." in quiet[1]


def test_every_rule_in_the_existing_engine_has_a_label():
    _, _, fr, _ = load_ml()
    ids = {name for name, _, _ in fr.HIGH_RULES} | {fr.IMBALANCE_RULE}
    assert ids == set(RULE_LABELS)
    assert rule_label("something_new") == "Something new"
