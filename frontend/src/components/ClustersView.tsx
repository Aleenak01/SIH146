import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, qs } from '../api/client';
import type { ClusterDetail, ClusterPage, ClusterSummary, EntityDetail } from '../api/types';
import { fmtBtc, fmtInt, fmtScore } from '../format';
import { ApiGraph, graphCaption } from '../graph/ApiGraph';
import { useCases } from '../state/cases';
import { useBackend } from '../state/data';
import { Panel, PredictionText, PriorityPill } from './bits';

/** Query-string helper that reads the live URL, so two quick changes cannot overwrite each other. */
function useParams2() {
  const [sp, setSp] = useSearchParams();
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setSp(next, { replace: true });
  };
  return { get: (k: string) => sp.get(k) ?? '', set };
}

/**
 * Related-entity clusters (inside Transactions / Network). Two kinds: wallets that share synthetic network
 * observations (an IP address, device or session) and communities found in the transfer graph (Louvain). A cluster
 * shows connected activity, not common ownership or wrongdoing. IP, device and session identifiers are synthetic.
 */
export function ClustersView() {
  const { get, set } = useParams2();
  const backend = useBackend();
  const runId = backend.analysis?.runId;
  const clusterId = get('cluster');
  const entity = get('entity');
  const method = get('method');
  const q = get('q');
  const leadsOnly = get('leads') === '1';
  const [page, setPage] = useState<ClusterPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .get<ClusterPage>(`/api/clusters${qs({ method: method || undefined, has_leads: leadsOnly ? true : undefined, q: q || undefined, sort: 'priority', limit: 200 })}`)
      .then(
        (p) => {
          if (!live) return;
          setPage(p);
          setError(null);
        },
        (e) => live && setError(e instanceof Error ? e.message : String(e)),
      );
    return () => {
      live = false;
    };
  }, [method, leadsOnly, q, runId]);

  return (
    <>
      {entity && <EntityPanel key={entity} spec={entity} onClose={() => set({ entity: null })} onOpen={(s) => set({ entity: s })} />}
      {clusterId && <ClusterPanel key={clusterId} id={clusterId} runId={runId} onClose={() => set({ cluster: null })} />}

      <div className="filters">
        <label className="field-inline">
          <span>Cluster type</span>
          <select value={method} onChange={(e) => set({ method: e.target.value || null })}>
            <option value="">Both</option>
            <option value="shared_network_observation">Shared network observation (synthetic)</option>
            <option value="transaction_community">Transaction community</option>
          </select>
        </label>
        <label className="field-inline">
          <span>Wallet or cluster ID</span>
          <input value={q} onChange={(e) => set({ q: e.target.value || null })} placeholder="e.g. wallet_350 or NET-0001" />
        </label>
        <label className="check">
          <input type="checkbox" checked={leadsOnly} onChange={(e) => set({ leads: e.target.checked ? '1' : null })} />
          Containing leads
        </label>
      </div>

      <Panel
        title="Clusters"
        note="Ordered by the highest priority among their wallets. Select a cluster to see its wallets, shared entities and graph. A cluster indicates connected activity, not common ownership or wrongdoing."
      >
        {error && <p className="field-error">Could not load clusters: {error}</p>}
        {!page && !error && <div className="viz-loading">Loading clusters…</div>}
        {page && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Cluster</th>
                  <th>Type</th>
                  <th className="num">Wallets</th>
                  <th className="num">Leads</th>
                  <th className="num">High priority</th>
                  <th>Top wallet</th>
                  <th className="num">Transactions</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((c) => (
                  <ClusterRow key={c.cluster_id} c={c} active={c.cluster_id === clusterId} onOpen={() => set({ cluster: c.cluster_id })} />
                ))}
                {page.items.length === 0 && (
                  <tr>
                    <td colSpan={7} className="empty">
                      No clusters match. Clusters are computed after each analysis run.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        {page && <p className="graph-foot">{fmtInt(page.total)} clusters{page.total > page.items.length ? ` (showing ${page.items.length})` : ''}. “Shared network observation” clusters come from synthetic IP / device / session observations.</p>}
      </Panel>
    </>
  );
}

function ClusterRow({ c, active, onOpen }: { c: ClusterSummary; active: boolean; onOpen: () => void }) {
  const p = c.priority_summary ?? {};
  return (
    <tr className={`row-link${active ? ' row-open' : ''}`} onClick={onOpen}>
      <td className="mono">{c.cluster_id}</td>
      <td>{c.method_label}</td>
      <td className="num mono">{c.wallet_count}</td>
      <td className="num mono">{p.lead_count ?? '—'}</td>
      <td className="num mono">{p.high_priority_count ?? '—'}</td>
      <td className="mono">{p.top_wallet ?? '—'}</td>
      <td className="num mono">{fmtInt(c.transaction_count)}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------------------------
function ClusterPanel({ id, runId, onClose }: { id: string; runId: number | null | undefined; onClose: () => void }) {
  const [d, setD] = useState<ClusterDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const store = useCases();
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [existing, setExisting] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<ClusterDetail>(`/api/clusters/${encodeURIComponent(id)}`).then(
      (x) => {
        if (!live) return;
        setD(x);
        setError(null);
        setTitle((t) => t || `Review of cluster ${x.cluster_id}`);
      },
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [id, runId]);

  if (error) {
    return (
      <Panel title={id} actions={<button className="btn btn-sm" onClick={onClose}>Close</button>}>
        <p className="field-error">{error}. Cluster ids can change when the analysis is re-run.</p>
      </Panel>
    );
  }
  if (!d) return <Panel title={id}><div className="viz-loading">Loading cluster…</div></Panel>;

  const p = d.priority_summary ?? {};
  const inCases = d.case_ids;
  const openCases = store.cases.filter((c) => c.status !== 'Closed' && !inCases.includes(c.case_id));

  const act = async (fn: () => Promise<string>) => {
    setBusy(true);
    setActionError(null);
    try {
      const caseId = await fn();
      navigate(`/cases/${caseId}`);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <Panel
      title={
        <>
          <span className="mono">{d.cluster_id}</span> · {d.method_label}
        </>
      }
      note={d.note}
      actions={
        <button type="button" className="btn btn-sm" onClick={onClose}>
          Close
        </button>
      }
      className="cluster-panel"
    >
      <div className="metrics metrics-3">
        <div className="metric">
          <div className="metric-label">Wallets</div>
          <div className="metric-value">{d.wallet_count}</div>
          <div className="metric-sub">{d.network_observation_count ? `${fmtInt(d.network_observation_count)} synthetic observations` : 'From transfers between them'}</div>
        </div>
        <div className="metric">
          <div className="metric-label">Leads</div>
          <div className="metric-value">{p.lead_count ?? 0}</div>
          <div className="metric-sub">
            {p.high_priority_count ?? 0} High priority · {p.anomalous_count ?? 0} ML-anomalous
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Highest combined result</div>
          <div className="metric-value mono">{typeof p.max_combined_score === 'number' ? fmtScore(p.max_combined_score) : '—'}</div>
          <div className="metric-sub">{p.top_wallet ? <>Top: <Link to={`/wallets/${p.top_wallet}`} className="mono">{p.top_wallet}</Link></> : 'Prototype fusion — not validated'}</div>
        </div>
      </div>

      <div className="table-wrap">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Wallet</th>
              <th>Priority</th>
              <th>ML</th>
              <th className="num">Combined</th>
              <th className="num">Transactions</th>
            </tr>
          </thead>
          <tbody>
            {d.wallets.map((w) => (
              <tr key={w.wallet_address}>
                <td>
                  <Link to={`/wallets/${w.wallet_address}`} className="mono wallet-link">
                    {w.wallet_address}
                  </Link>
                  {w.is_lead && <span className="rule-tag rule-tag-inline">lead</span>}
                </td>
                <td>{w.priority_level ? <PriorityPill level={w.priority_level as 'High' | 'Medium' | 'Low'} /> : '—'}</td>
                <td>{w.ml_prediction ? <PredictionText flagged={w.ml_prediction === 'Anomalous'} /> : '—'}</td>
                <td className="num mono">{w.combined_score !== null ? `#${w.priority_rank} · ${fmtScore(w.combined_score)}` : '—'}</td>
                <td className="num mono">{w.transaction_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(d.devices.length > 0 || d.ip_addresses.length > 0 || d.sessions.length > 0) && (
        <div className="related-block">
          <h4>
            Shared network entities <span className="rule-tag">synthetic</span>
          </h4>
          <p>
            {d.devices.map((x) => (
              <Link key={x} to={`/network?view=clusters&cluster=${d.cluster_id}&entity=device:${x}`} className="mono chip-link">
                {x}
              </Link>
            ))}
            {d.ip_addresses.map((x) => (
              <Link key={x} to={`/network?view=clusters&cluster=${d.cluster_id}&entity=ip:${x}`} className="mono chip-link">
                {x}
              </Link>
            ))}
            {d.sessions.length > 0 && <span className="muted"> {d.sessions.length} session{d.sessions.length === 1 ? '' : 's'}</span>}
          </p>
        </div>
      )}

      {d.internal_relationships.length > 0 && (
        <div className="related-block">
          <h4>Transfers inside the cluster</h4>
          <ul className="cluster-mini">
            {d.internal_relationships.slice(0, 10).map((r) => (
              <li key={`${r.source}>${r.target}`}>
                <span className="mono">{r.source}</span> → <span className="mono">{r.target}</span> <span className="muted">· {r.transfers} transfers · {fmtBtc(r.total_btc)} BTC</span>
              </li>
            ))}
            {d.internal_relationships.length > 10 && <li className="muted">and {d.internal_relationships.length - 10} more</li>}
          </ul>
        </div>
      )}

      <ApiGraph graph={d.graph} height={480} />
      <p className="graph-foot">{graphCaption(d.graph)}. Outlined wallets are ML-flagged.</p>

      {store.available && (
        <div className="cluster-actions">
          {inCases.length > 0 && (
            <p className="muted">
              Already in: {inCases.map((c) => (
                <Link key={c} to={`/cases/${c}`} className="mono chip-link">
                  {c}
                </Link>
              ))}
            </p>
          )}
          <div className="add-item">
            <span>New case</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Case title" maxLength={200} />
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={busy || !title.trim()}
              onClick={() => void act(async () => (await store.createCase({ title, items: [{ type: 'cluster', id: d.cluster_id }] })).case_id)}
            >
              Create case from cluster
            </button>
          </div>
          {openCases.length > 0 && (
            <div className="add-item">
              <span>Existing case</span>
              <select value={existing} onChange={(e) => setExisting(e.target.value)} aria-label="Existing case">
                <option value="">Choose…</option>
                {openCases.map((c) => (
                  <option key={c.case_id} value={c.case_id}>
                    {c.case_id} · {c.title}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-sm"
                disabled={busy || !existing}
                onClick={() => void act(async () => { await store.addItem(existing, 'cluster', d.cluster_id); return existing; })}
              >
                Add to case
              </button>
            </div>
          )}
          {actionError && <p className="field-error" role="alert">{actionError}</p>}
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------------
function EntityPanel({ spec, onClose, onOpen }: { spec: string; onClose: () => void; onOpen: (spec: string) => void }) {
  const [type, ...rest] = spec.split(':');
  const id = rest.join(':');
  const [d, setD] = useState<EntityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<EntityDetail>(`/api/network/entities/${encodeURIComponent(type)}/${encodeURIComponent(id)}`).then(
      (x) => live && setD(x),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [type, id]);

  const label = type === 'ip' ? 'IP address' : type === 'device' ? 'Device' : 'Session';
  return (
    <Panel
      title={
        <>
          {label} <span className="mono">{id}</span> <span className="rule-tag">synthetic</span>
        </>
      }
      note={d?.note ?? 'Synthetic network observation generated for the demo. Bitcoin transactions carry no IP address, device or session information.'}
      actions={
        <button type="button" className="btn btn-sm" onClick={onClose}>
          Close
        </button>
      }
    >
      {error && <p className="field-error">{error}</p>}
      {!d && !error && <div className="viz-loading">Loading…</div>}
      {d && (
        <div className="kv">
          <dl>
            <dt>Observations</dt>
            <dd>
              {fmtInt(d.observation_count)} across {fmtInt(d.transactions)} transactions
            </dd>
            <dt>Wallets seen with it</dt>
            <dd>
              {d.wallets.slice(0, 30).map((w) => (
                <Link key={w} to={`/wallets/${w}`} className="mono chip-link">
                  {w}
                </Link>
              ))}
              {d.wallets.length > 30 && <span className="muted"> and {d.wallets.length - 30} more</span>}
            </dd>
            {(['devices', 'ips'] as const).map((k) =>
              d.related[k].length > 0 ? (
                <div key={k} className="kv-pair">
                  <dt>{k === 'devices' ? 'Devices seen with it' : 'IP addresses seen with it'}</dt>
                  <dd>
                    {d.related[k].slice(0, 20).map((x) => (
                      <button key={x} type="button" className="mono chip-link" onClick={() => onOpen(`${k === 'devices' ? 'device' : 'ip'}:${x}`)}>
                        {x}
                      </button>
                    ))}
                  </dd>
                </div>
              ) : null,
            )}
          </dl>
        </div>
      )}
    </Panel>
  );
}
