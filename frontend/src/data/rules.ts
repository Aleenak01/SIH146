import type { FeatureKey } from './types';

// Display metadata for the rule names emitted by ml/forensic_rules.py (HIGH_RULES + imbalance rule).
// This is labelling only: which rule fired, and its explanation text, always come from the CSV.
export const RULES: Record<string, { label: string; feature: FeatureKey }> = {
  high_transaction_count: { label: 'High transaction count', feature: 'transaction_count' },
  high_transaction_frequency: { label: 'High transaction frequency', feature: 'transaction_frequency' },
  activity_burst: { label: 'Activity burst', feature: 'activity_burst' },
  long_dormancy: { label: 'Long dormancy', feature: 'dormancy_duration' },
  fan_out: { label: 'High fan-out', feature: 'fan_out' },
  fan_in: { label: 'High fan-in', feature: 'fan_in' },
  many_counterparties: { label: 'Many counterparties', feature: 'unique_counterparties' },
  repeated_relationships: { label: 'Repeated relationships', feature: 'repeated_connections' },
  high_received_volume: { label: 'High received volume', feature: 'total_received_btc' },
  high_sent_volume: { label: 'High sent volume', feature: 'total_sent_btc' },
  incoming_outgoing_imbalance: { label: 'Incoming/outgoing imbalance', feature: 'incoming_outgoing_ratio' },
};

export const TOTAL_RULES = Object.keys(RULES).length;

export function ruleLabel(name: string): string {
  return RULES[name]?.label ?? name.replace(/_/g, ' ');
}

/**
 * forensic_findings joins one sentence per triggered rule, in the same order as triggered_rules.
 * Each sentence ends with ")." so we split on that boundary. If the sentence count does not match
 * the rule count we return null and the caller shows the unsplit text instead of guessing.
 */
export function splitFindings(findings: string, ruleNames: string[]): string[] | null {
  if (ruleNames.length === 0) return [];
  const parts = findings.split(/(?<=\)\.)\s+(?=[A-Z])/);
  return parts.length === ruleNames.length ? parts : null;
}

export function parseRuleNames(triggered: string): string[] {
  return !triggered || triggered === 'none' ? [] : triggered.split(';').filter(Boolean);
}

/** Features grouped as in ml/feature_engineering.py. Units are from that script's docstring. */
export const FEATURE_GROUPS: {
  title: string;
  features: { key: FeatureKey; label: string; unit?: string; digits?: number }[];
}[] = [
  {
    title: 'Transaction activity',
    features: [
      { key: 'transaction_count', label: 'Transactions', digits: 0 },
      { key: 'incoming_count', label: 'Incoming', digits: 0 },
      { key: 'outgoing_count', label: 'Outgoing', digits: 0 },
      { key: 'avg_transaction_amount', label: 'Average amount', unit: 'BTC', digits: 4 },
    ],
  },
  {
    title: 'Value flow',
    features: [
      { key: 'total_received_btc', label: 'Total received', unit: 'BTC', digits: 3 },
      { key: 'total_sent_btc', label: 'Total sent', unit: 'BTC', digits: 3 },
      { key: 'incoming_outgoing_ratio', label: 'Received / sent ratio (cap 100)', digits: 2 },
    ],
  },
  {
    title: 'Timing',
    features: [
      { key: 'transaction_frequency', label: 'Frequency', unit: 'tx/day', digits: 3 },
      { key: 'activity_burst', label: 'Activity burst (max tx in 1 h)', digits: 0 },
      { key: 'dormancy_duration', label: 'Longest dormancy', unit: 'h', digits: 0 },
      { key: 'time_since_previous_tx', label: 'Time since previous tx', unit: 'h', digits: 0 },
      { key: 'avg_transaction_interval', label: 'Average interval', unit: 'h', digits: 0 },
    ],
  },
  {
    title: 'Network',
    features: [
      { key: 'fan_in', label: 'Fan-in (distinct senders)', digits: 0 },
      { key: 'fan_out', label: 'Fan-out (distinct recipients)', digits: 0 },
      { key: 'unique_counterparties', label: 'Unique counterparties', digits: 0 },
      { key: 'wallet_degree', label: 'Wallet degree', digits: 0 },
      { key: 'repeated_connections', label: 'Repeated connections', digits: 0 },
      { key: 'hop_distance', label: 'Mean hop distance', digits: 2 },
    ],
  },
];
