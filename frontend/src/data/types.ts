// Shapes of the existing pipeline outputs. Nothing here is computed by the frontend except
// descriptive aggregation (counts, ranks, percentile positions); scores come from the CSVs as-is.

export type MlPrediction = 'Anomalous' | 'Normal';

export type EvidenceLevel =
  | 'No specific rule triggered'
  | 'Single behavioural indicator'
  | 'Multiple behavioural indicators'
  | 'Multiple strong behavioural indicators';

/** One row of data/fusion_results.csv (forensic + ML evidence + prototype combined score). */
export interface FusionRow {
  wallet_address: string;
  forensic_score: number;
  forensic_rule_count: number;
  forensic_evidence_level: EvidenceLevel;
  forensic_triggered_rules: string; // ';'-separated rule names, or 'none'
  forensic_findings: string; // backend explanation text for all triggered rules
  ml_anomaly_score: number;
  ml_anomaly_prediction: MlPrediction;
  combined_score: number;
  forensic_weight: number;
  ml_weight: number;
  valid: boolean;
  warnings: string;
  errors: string;
}

/** One row of data/wallet_behavior_features.csv (the 18 Isolation Forest input features). */
export interface FeatureRow {
  wallet_address: string;
  transaction_count: number;
  incoming_count: number;
  outgoing_count: number;
  total_received_btc: number;
  total_sent_btc: number;
  avg_transaction_amount: number;
  time_since_previous_tx: number;
  avg_transaction_interval: number;
  transaction_frequency: number;
  dormancy_duration: number;
  activity_burst: number;
  incoming_outgoing_ratio: number;
  fan_in: number;
  fan_out: number;
  unique_counterparties: number;
  wallet_degree: number;
  repeated_connections: number;
  hop_distance: number;
}

export type FeatureKey = Exclude<keyof FeatureRow, 'wallet_address'>;

/** One transfer A -> B. The raw CSV stores each transfer as two mirrored rows; we keep one. */
export interface Transfer {
  ts: number; // epoch ms (timestamps in the CSV are naive; treated as UTC)
  from: string;
  to: string;
  amountBtc: number;
  inputCount: number;
  outputCount: number;
  /** Line number of the Outgoing record in synthetic_bitcoin_transactions.csv (the file has no transaction ID). Absent when loaded from the backend. */
  row?: number;
  /** Backend transaction ID (syn-000001 ...). Absent when loaded from the bundled CSV. */
  id?: string;
}

/** Joined, per-wallet view used by every screen. */
export interface WalletRecord {
  id: string;
  fusion: FusionRow;
  features: FeatureRow;
  /** 1 = highest combined_score among all wallets. */
  priorityRank: number;
  flagged: boolean; // ML prediction === 'Anomalous'
  /** Only when loaded from the backend: lead / priority / review state (absent in the bundled-CSV fallback). */
  lead?: LeadInfo;
}

export interface LeadInfo {
  priorityLevel: 'High' | 'Medium' | 'Low';
  isLead: boolean;
  reasons: string[];
  caseIds: string[];
  review: 'Unreviewed' | 'Under Review' | 'Case Created';
}
