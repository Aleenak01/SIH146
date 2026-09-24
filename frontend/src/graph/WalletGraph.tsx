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
import { fmtNum, fmtScore } from '../format';
import { useTheme } from '../state/theme';
import type { GraphModel } from './model';

interface WalletNodeData extends Record<string, unknown> {
  id: string;
  role: 'focus' | 'counterparty';
  flagged: boolean;
  mlScore: number | null;
  flow?: string;
}

function WalletNode({ data }: NodeProps<Node<WalletNodeData>>) {
  const cls = ['wf-node', data.role === 'focus' ? 'focus' : '', data.flagged ? 'flagged' : ''].join(' ');
  return (
    <div className={cls}>
      {/* Both sides carry a source and a target handle so any pair of wallets can be joined. */}
      <Handle id="l-t" type="target" position={Position.Left} isConnectable={false} />
      <Handle id="l-s" type="source" position={Position.Left} isConnectable={false} />
      <div className="wf-id">{data.id}</div>
      <div className="wf-meta">
        {data.mlScore !== null ? `ML ${fmtScore(data.mlScore)}` : 'not scored'}
        {data.flagged && <span className="wf-flag">Anomalous</span>}
      </div>
      {data.flow && <div className="wf-flow">{data.flow}</div>}
      <Handle id="r-t" type="target" position={Position.Right} isConnectable={false} />
      <Handle id="r-s" type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

const nodeTypes: NodeTypes = { wallet: WalletNode };

const COL_X = { left: 0, center: 400, right: 800 } as const;
const ROW_H = 80;

function layout(model: GraphModel): { nodes: Node<WalletNodeData>[]; edges: Edge[] } {
  const cols = { left: [] as typeof model.nodes, center: [] as typeof model.nodes, right: [] as typeof model.nodes };
  for (const n of model.nodes) cols[n.side].push(n);
  const sideOf = new Map(model.nodes.map((n) => [n.id, n.side]));

  const nodes: Node<WalletNodeData>[] = [];
  (Object.keys(cols) as (keyof typeof cols)[]).forEach((side) => {
    cols[side].forEach((n, i) => {
      const flow =
        n.role === 'counterparty'
          ? n.toFocus && n.fromFocus
            ? `${n.toFocus} + ${n.fromFocus} tx · ${fmtNum(n.btc ?? 0, 2)} BTC`
            : `${n.toFocus || n.fromFocus || 0} tx · ${fmtNum(n.btc ?? 0, 2)} BTC`
          : undefined;
      nodes.push({
        id: n.id,
        type: 'wallet',
        position: { x: COL_X[side], y: (i - (cols[side].length - 1) / 2) * ROW_H },
        data: { id: n.id, role: n.role, flagged: n.flagged, mlScore: n.mlScore, flow },
        draggable: false,
      });
    });
  });

  const edges: Edge[] = model.edges.map((e) => {
    const xs = COL_X[sideOf.get(e.source)!];
    const xt = COL_X[sideOf.get(e.target)!];
    let sourceHandle: string;
    let targetHandle: string;
    if (xs < xt) [sourceHandle, targetHandle] = ['r-s', 'l-t'];
    else if (xs > xt) [sourceHandle, targetHandle] = ['l-s', 'r-t'];
    else if (xs === COL_X.left) [sourceHandle, targetHandle] = ['l-s', 'l-t'];
    else [sourceHandle, targetHandle] = ['r-s', 'r-t'];

    const count = e.forwardCount + e.backwardCount;
    const marker = { type: MarkerType.ArrowClosed, color: 'var(--graph-edge)', width: 14, height: 14 } as const;
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle,
      targetHandle,
      markerEnd: marker,
      ...(e.backwardCount > 0 ? { markerStart: marker } : {}),
      // Repeated relationships are drawn heavier.
      style: { stroke: 'var(--graph-edge)', strokeWidth: Math.min(3.5, 1.25 + 0.5 * (count - 1)) },
      ...(e.kind === 'link'
        ? {
            label: `${count} tx`,
            labelStyle: { fill: 'var(--text-2)', fontSize: 11 },
            labelBgStyle: { fill: 'var(--surface-2)', fillOpacity: 0.95 },
            labelBgPadding: [4, 2] as [number, number],
          }
        : {}),
      selectable: false,
    };
  });
  return { nodes, edges };
}

/**
 * Wallet relationship graph (React Flow). Takes a framework-independent GraphModel built from the
 * transaction data. By default selecting a wallet opens its investigation; `onSelect` overrides
 * that (the network explorer uses it to re-centre the graph).
 */
export function WalletGraph({
  model,
  height = 560,
  onSelect,
}: {
  model: GraphModel;
  height?: number;
  onSelect?: (walletId: string) => void;
}) {
  const { theme } = useTheme();
  const navigate = useNavigate();
  const { nodes, edges } = useMemo(() => layout(model), [model]);
  const focusOnly = model.focusIds.length === 1;

  return (
    <div className="wf-frame" style={{ height }}>
      <ReactFlow
        key={`${model.focusIds.join(',')}:${model.nodes.length}:${model.edges.length}`}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode={theme}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.3}
        maxZoom={1.6}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={false}
        onNodeClick={(_, n) => {
          if (n.data.role === 'focus' && focusOnly) return;
          if (onSelect) onSelect(n.id);
          else navigate(`/wallets/${n.id}`);
        }}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={20} size={1} color="var(--grid)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
