"""Human-readable labels for the rule ids emitted by ml/forensic_rules.py (labels only; the rules live in ml/)."""

RULE_LABELS = {
    "high_transaction_count": "High transaction count",
    "high_transaction_frequency": "High transaction frequency",
    "activity_burst": "Activity burst",
    "long_dormancy": "Long dormancy",
    "fan_out": "High fan-out",
    "fan_in": "High fan-in",
    "many_counterparties": "Many counterparties",
    "repeated_relationships": "Repeated relationships",
    "high_received_volume": "High received volume",
    "high_sent_volume": "High sent volume",
    "incoming_outgoing_imbalance": "Incoming/outgoing imbalance",
}


def rule_label(rule_id: str) -> str:
    return RULE_LABELS.get(rule_id, rule_id.replace("_", " ").capitalize())
