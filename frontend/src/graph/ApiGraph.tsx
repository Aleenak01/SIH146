import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { GraphEdge, GraphNode, GraphOut } from '../api/types';
import { fmtScore } from '../format';
import { useTheme } from '../state/theme';

// Renders a graph returned by the backend (/api/graph, cluster and case graphs, and Phase 2's /api/entity-graph).
// Nodes are laid out in columns by type; IP, device and session nodes are SYNTHETIC network observations and say so.
// The entity-graph lanes ('entity', 'address', 'ip', 'asn', 'country') are appended after the wallet-graph ones, so
// a wallet-focused graph (which never contains those types) lays out exactly as before this was added.

const LANES: string[] = ['ip_observation', 'device', 'session', 'wallet', 'transaction', 'entity', 'address', 'ip', 'asn', 'country'];
const KIND: Record<string, string> = {
  ip_observation: 'IP (synthetic)', device: 'Device (synthetic)', session: 'Session (synthetic)', transaction: 'Transaction',
  ip: 'IP (synthetic)', asn: 'ASN (synthetic)', country: 'Country (synthetic)', address: 'Address',
};
const NODE_W = 200;
const ROW_H = 70;

interface NData extends Record<string, unknown> {
  node: GraphNode;
}

function Handles() {
  return (
    <>
      <Handle id="l-t" type="target" position={Position.Left} isConnectable={false} />
      <Handle id="l-s" type="source" position={Position.Left} isConnectable={false} />
      <Handle id="r-t" type="target" position={Position.Right} isConnectable={false} />
      <Handle id="r-s" type="source" position={Position.Right} isConnectable={false} />
    </>
  );
}

function WalletNode({ data }: NodeProps<Node<NData>>) {
  const n = data.node;
  const d = n.data;
  const flagged = d.ml_prediction === 'Anomalous';
  return (
    <div className={`wf-node${d.is_focus ? ' focus' : ''}${flagged ? ' flagged' : ''}`}>
      <Handles />
      <div className="wf-id">{n.label}</div>
      <div className="wf-meta">
        {typeof d.combined_score === 'number' ? `#${d.priority_rank} · ${fmtScore(d.combined_score)}` : 'not scored'}
        {d.is_lead && <span className="wf-flag">Lead</span>}
      </div>
    </div>
  );
}

function InfraNode({ data }: NodeProps<Node<NData>>) {
  const n = data.node;
  const d = n.data;
  const detail =
    n.type === 'device'
      ? `${d.wallet_count ?? 1} wallet${d.wallet_count === 1 ? '' : 's'}${d.network_type ? ` · ${d.network_type}` : ''}`
      : n.type === 'ip_observation'
        ? `${d.observation_count ?? 1} observation${d.observation_count === 1 ? '' : 's'}`
        : n.type === 'session'
          ? `${d.observation_count ?? 1} observation${d.observation_count === 1 ? '' : 's'}`
          : n.type === 'asn' && d.asn_org
            ? String(d.asn_org)
            : d.amount_btc !== undefined
              ? `${d.amount_btc} BTC`
              : '';
  return (
    <div className={`wf-node infra infra-${n.type}${d.is_focus ? ' focus' : ''}`} title={String(d.note ?? '')}>
      <Handles />
      <div className="wf-kind">{KIND[n.type] ?? n.type}</div>
      <div className="wf-id">{n.label}</div>
      {detail && <div className="wf-meta">{detail}</div>}
    </div>
  );
}

/** An address entity (Phase 2's /api/entity-graph): the "main" object of that graph, analogous to a wallet node. */
function EntityNode({ data }: NodeProps<Node<NData>>) {
  const n = data.node;
  const d = n.data;
  return (
    <div className={`wf-node${d.is_focus ? ' focus' : ''}`}>
      <Handles />
      <div className="wf-id">{n.label}</div>
      <div className="wf-meta">
        {d.address_count ?? 0} address{d.address_count === 1 ? '' : 'es'} · {d.transaction_count ?? 0} tx
      </div>
    </div>
  );
}

const nodeTypes: NodeTypes = { wallet: WalletNode, infra: InfraNode, entity: EntityNode };

function layout(g: GraphOut): { nodes: Node<NData>[]; edges: Edge[] } {
  const byType = new Map<string, GraphNode[]>();
  for (const n of g.nodes) byType.set(n.type, [...(byType.get(n.type) ?? []), n]);
  const pos = new Map<string, { x: number; y: number }>();
  let x = 0;
  for (const t of LANES) {
    const list = byType.get(t);
    if (!list?.length) continue;
    // Focus and most important nodes first.
    list.sort((a, b) => Number(!!b.data.is_focus) - Number(!!a.data.is_focus) || (a.data.priority_rank ?? 1e9) - (b.data.priority_rank ?? 1e9) || a.label.localeCompare(b.label));
    const cols = list.length <= 12 ? 1 : list.length <= 30 ? 2 : 3;
    const rows = Math.ceil(list.length / cols);
    list.forEach((n, i) => pos.set(n.id, { x: x + (i % cols) * (NODE_W + 24), y: (Math.floor(i / cols) - (rows - 1) / 2) * ROW_H }));
    x += cols * (NODE_W + 24) + 90;
  }

  const nodes: Node<NData>[] = g.nodes.map((n) => ({
    id: n.id,
    type: n.type === 'wallet' ? 'wallet' : n.type === 'entity' ? 'entity' : 'infra',
    position: pos.get(n.id) ?? { x: 0, y: 0 },
    data: { node: n },
    draggable: false,
  }));

  const nodeById = new Map(g.nodes.map((n) => [n.id, n]));
  const edges: Edge[] = g.edges
    .filter((e: GraphEdge) => nodeById.has(e.source) && nodeById.has(e.target))
    .map((e) => {
      const ps = pos.get(e.source)!;
      const pt = pos.get(e.target)!;
      const [sh, th] = ps.x < pt.x ? ['r-s', 'l-t'] : ps.x > pt.x ? ['l-s', 'r-t'] : ['r-s', 'r-t'];
      const transfer = e.type === 'sent_to' || e.type === 'received_from';
      // Entity-graph edges: 'shared_ip' is the entity<->entity analog of same_device/same_session (a possible link,
      // never proof); the rest are directed structural edges (address belongs to entity, was an input/output of a
      // transaction, or that transaction's synthetic flow went to this IP/ASN/country) -- drawn like a transfer
      // (an arrowhead so direction is visible) but without a transfer count to size the line by.
      const derived = e.type === 'same_device' || e.type === 'same_session' || e.type === 'shared_ip';
      const directed = e.type === 'in_entity' || e.type === 'input_of' || e.type === 'output_to' || e.type === 'sent_from_ip' || e.type === 'in_asn' || e.type === 'in_country';
      const transfers = Number(e.data.transfers ?? 1);
      const label = e.type === 'same_device' ? 'same device' : e.type === 'same_session' ? 'same session' : e.type === 'shared_ip' ? 'shared IP' : undefined;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: sh,
        targetHandle: th,
        ...(transfer || directed ? { markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--graph-edge)', width: 14, height: 14 } } : {}),
        style: {
          stroke: derived ? 'var(--accent)' : 'var(--graph-edge)',
          strokeWidth: transfer ? Math.min(3.5, 1.25 + 0.5 * (transfers - 1)) : 1.25,
          strokeDasharray: transfer || directed ? undefined : derived ? '2 4' : '5 4',
        },
        ...(label ? { label, labelStyle: { fill: 'var(--text-2)', fontSize: 11 }, labelBgStyle: { fill: 'var(--surface-2)' } } : {}),
        selectable: false,
      } as Edge;
    });
  return { nodes, edges };
}

export function ApiGraph({ graph, height = 560, onWallet }: { graph: GraphOut; height?: number; onWallet?: (wallet: string) => void }) {
  const { theme } = useTheme();
  const navigate = useNavigate();
  const { nodes, edges } = useMemo(() => layout(graph), [graph]);

  return (
    <div className="wf-frame" style={{ height }}>
      <ReactFlow
        key={`${nodes.length}:${edges.length}:${graph.nodes[0]?.id ?? ''}`}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode={theme}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.15}
        maxZoom={1.6}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={false}
        onNodeClick={(_, n) => {
          const node = (n.data as NData).node;
          if (node.type !== 'wallet') return;
          if (onWallet) onWallet(node.label);
          else navigate(`/wallets/${node.label}`);
        }}
      >
        <Background gap={20} size={1} color="var(--grid)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

/** One line under a graph: what is drawn, what was left out, and the synthetic-data caveat. */
export function graphCaption(g: GraphOut): string {
  const parts = Object.entries(g.counts.nodes ?? {}).map(([k, v]) => `${v} ${k.replace('_', ' ')}${v === 1 ? '' : 's'}`);
  const omitted = Object.entries(g.omitted).filter(([, v]) => v > 0).map(([k, v]) => `${v} ${k.replace('_', ' ')}`);
  return `${parts.join(' · ')}${g.truncated ? ` — truncated, not shown: ${omitted.join(', ')}` : ''}`;
}
