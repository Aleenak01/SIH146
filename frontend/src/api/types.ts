// Response shapes of the backend endpoints the frontend uses (see backend/schemas.py).

export interface Overview {
  transactions_total: number;
  wallets_total: number;
  by_source: Record<string, Record<string, number>>;
  first_transaction_at: string | null;
  last_transaction_at: string | null;
  last_analysis_at: string | null;
  analysis_stale: boolean;
  anomalous_wallets: number | null;
  leads_total: number | null;
  network_observations_total: number;
  clusters_total: number;
  cases_total: number;
  active_cases: number;
}

export interface MonitorStatus {
  status: 'active' | 'stopped';
  data_source: { kind: string; label: string; is_real_data: boolean; note: string };
  interval_seconds: number;
  auto_analysis: boolean;
  analysis_mode: string;
  ticks: number;
  transactions_total: number;
  wallets_total: number;
  last_transaction_at: string | null;
  last_ingest_at: string | null;
  last_ingest_origin: string | null;
  last_analysis_at: string | null;
  last_analysis_run_id: number | null;
  last_analysis_trigger: string | null;
  analysis_in_progress: boolean;
  analysis_stale: boolean;
  leads_total: number;
  new_leads_last_run: number;
  stream: { enabled: boolean; rate_per_minute: number; last_scenario: { wallet: string; transactions: number } | null };
  last_error: string | null;
}

export interface ApiTransaction {
  transaction_id: string;
  timestamp: string;
  sender_wallet: string;
  receiver_wallet: string;
  amount_btc: number;
  input_count: number;
  output_count: number;
  source: string;
}

export type PriorityLevel = 'High' | 'Medium' | 'Low';
export type ReviewState = 'Unreviewed' | 'Under Review' | 'Case Created';

export interface BulkWallet {
  wallet_address: string;
  ml_score: number;
  ml_prediction: 'Anomalous' | 'Normal';
  forensic_score: number;
  forensic_rule_count: number;
  evidence_level: string;
  triggered_rules: string[];
  findings: string[];
  combined_score: number;
  priority_rank: number;
  priority_level: PriorityLevel;
  is_lead: boolean;
  reasons: string[];
  case_ids: string[];
  review_status: ReviewState;
  features: Record<string, number>;
}

export interface BulkAnalysis {
  run_id: number | null;
  source: string;
  finished_at: string | null;
  stale: boolean;
  forensic_weight: number | null;
  ml_weight: number | null;
  fusion_note: string;
  scored_wallets: number;
  unscored_wallets: number;
  wallets: BulkWallet[];
}

export interface ClusterSummary {
  cluster_id: string;
  method: 'shared_network_observation' | 'transaction_community';
  method_label: string;
  source: string;
  entity_count: number;
  wallet_count: number;
  transaction_count: number;
  network_observation_count: number;
  priority_summary: Record<string, any> | null;
  updated_at: string;
}

export interface ClusterPage {
  total: number;
  items: ClusterSummary[];
}

export interface ClusterWallet {
  wallet_address: string;
  is_lead: boolean;
  ml_prediction: string | null;
  priority_level: string | null;
  priority_rank: number | null;
  combined_score: number | null;
  transaction_count: number;
}

export interface ClusterDetail extends ClusterSummary {
  wallets: ClusterWallet[];
  devices: string[];
  ip_addresses: string[];
  sessions: string[];
  internal_relationships: { source: string; target: string; transfers: number; total_btc: number }[];
  graph: GraphOut;
  case_ids: string[];
  note: string;
}

export interface GraphNode {
  id: string;
  type: 'wallet' | 'transaction' | 'ip_observation' | 'device' | 'session';
  label: string;
  data: Record<string, any>;
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  data: Record<string, any>;
}
export interface GraphOut {
  focus: Record<string, any>;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  omitted: Record<string, number>;
  counts: Record<string, Record<string, number>>;
  notes: string[];
}

export interface Observation {
  observation_id: string;
  transaction_id: string | null;
  wallet_address: string;
  observed_party: 'sender' | 'receiver' | null;
  ip_address: string | null;
  device_id: string | null;
  user_agent: string | null;
  network_type: string | null;
  session_id: string | null;
  geo_region: string | null;
  observed_at: string | null;
}
export interface ObservationPage {
  total: number;
  note: string;
  items: Observation[];
}

export interface EntityDetail {
  entity_type: string;
  entity_id: string;
  observation_count: number;
  wallets: string[];
  transactions: number;
  related: { devices: string[]; ips: string[]; sessions: string[] };
  first_observed_at: string | null;
  last_observed_at: string | null;
  note: string;
}

export type CaseStatus = 'Open' | 'Under investigation' | 'Closed';
export type CasePriority = 'High' | 'Medium' | 'Low';
export type ItemType = 'lead' | 'wallet' | 'transaction' | 'cluster';

export interface CaseSummary {
  case_id: string;
  title: string;
  status: CaseStatus;
  priority: CasePriority | null;
  assigned_to: string | null;
  created_at: string;
  updated_at: string;
  item_counts: Record<string, number>;
}

export interface CaseItem {
  item_type: ItemType;
  item_id: string;
  added_at: string;
  evidence_snapshot: Record<string, any> | null;
  current: Record<string, any> | null;
}

export interface CaseHistoryEntry {
  entry_id: number;
  at: string;
  actor: string;
  action: string;
  detail: string | null;
}

export interface CaseDetail extends CaseSummary {
  description: string | null;
  items: CaseItem[];
  history: CaseHistoryEntry[];
  notes: CaseHistoryEntry[];
  wallets: string[];
  related_transactions: { total: number; [k: string]: unknown };
  disclaimer: string;
}

export interface SearchHit {
  type: string;
  id: string;
  label: string;
  subtitle: string | null;
  match: string;
  data: Record<string, any>;
}
export interface SearchOut {
  query: string;
  total: number;
  categories: Record<string, { total: number; items: SearchHit[] }>;
  notes: string[];
}

export interface SettingsView {
  settings: { monitor_enabled: boolean; interval_seconds: number; auto_analysis: boolean; stream_enabled: boolean; stream_rate_per_minute: number };
  saved_keys: string[];
  data_source: { mode: string; label: string; is_real_data: boolean; real_bitcoin: { available: boolean; configured: boolean; status: string; note: string } };
  limits: { interval_seconds: [number, number]; stream_rate_per_minute: [number, number] };
  secrets_note: string;
}
