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

// Renders a graph returned by the backend (/api/graph, cluster and case graphs). Nodes are laid out in
// columns by type; IP, device and session nodes are SYNTHETIC network observations and say so.

const LANES: GraphNode['type'][] = ['ip_observation', 'device', 'session', 'wallet', 'transaction'];
const KIND: Record<string, string> = { ip_observation: 'IP (synthetic)', device: 'Device (synthetic)', session: 'Session (synthetic)', transaction: 'Transaction' };
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
          : d.amount_btc !== undefined
            ? `${d.amount_btc} BTC`
            : '';
  return (
    <div className={`wf-node infra infra-${n.type}${d.is_focus ? ' focus' : ''}`} title={String(d.note ?? '')}>
      <Handles />
      <div className="wf-kind">{KIND[n.type]}</div>
      <div className="wf-id">{n.label}</div>
      {detail && <div className="wf-meta">{detail}</div>}
    </div>
  );
}

const nodeTypes: NodeTypes = { wallet: WalletNode, infra: InfraNode };

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
    type: n.type === 'wallet' ? 'wallet' : 'infra',
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
      const derived = e.type === 'same_device' || e.type === 'same_session';
      const transfers = Number(e.data.transfers ?? 1);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: sh,
        targetHandle: th,
        ...(transfer ? { markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--graph-edge)', width: 14, height: 14 } } : {}),
        style: {
          stroke: derived ? 'var(--accent)' : 'var(--graph-edge)',
          strokeWidth: transfer ? Math.min(3.5, 1.25 + 0.5 * (transfers - 1)) : 1.25,
          strokeDasharray: transfer ? undefined : derived ? '2 4' : '5 4',
        },
        ...(derived ? { label: e.type === 'same_device' ? 'same device' : 'same session', labelStyle: { fill: 'var(--text-2)', fontSize: 11 }, labelBgStyle: { fill: 'var(--surface-2)' } } : {}),
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
