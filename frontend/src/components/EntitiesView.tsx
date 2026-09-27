import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, qs } from '../api/client';
import type { AddressEntityDetail, EntityPage, EntitySummary, GraphOut } from '../api/types';
import { fmtDateTime, fmtInt } from '../format';
import { ApiGraph, graphCaption } from '../graph/ApiGraph';
import { useCases } from '../state/cases';
import { Panel } from './bits';

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
 * Address entities: groups of addresses inferred to be under common ownership by the common-input-ownership
 * heuristic (co-spent inputs). Unlike the Clusters tab (shared network observations or dense transfer activity,
 * which is connected activity, not ownership), an entity here IS an ownership inference — a heuristic, not proof.
 */
export function EntitiesView() {
  const { get, set } = useParams2();
  const entityId = get('entity');
  const q = get('q');
  const [page, setPage] = useState<EntityPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<EntityPage>(`/api/entities${qs({ q: q || undefined, sort: 'size', limit: 200 })}`).then(
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
  }, [q]);

  return (
    <>
      {entityId && <EntityPanel key={entityId} id={entityId} onClose={() => set({ entity: null })} />}

      <div className="filters">
        <label className="field-inline">
          <span>Address or entity ID</span>
          <input value={q} onChange={(e) => set({ q: e.target.value || null })} placeholder="e.g. addr_1a2b… or CIO-addr_1a2b" />
        </label>
      </div>

      <Panel
        title="Address entities"
        note="Groups of addresses the common-input-ownership heuristic infers belong to a single wallet holder, because they were spent together as inputs of the same transaction. This IS an ownership inference — a heuristic, not proof. Select a row to see its member addresses, correlated network findings and graph."
      >
        {error && <p className="field-error">Could not load entities: {error}</p>}
        {!page && !error && <div className="viz-loading">Loading entities…</div>}
        {page && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th className="num">Addresses</th>
                  <th>Linked wallets</th>
                  <th className="num">Transactions</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((e) => (
                  <EntityRow key={e.entity_id} e={e} active={e.entity_id === entityId} onOpen={() => set({ entity: e.entity_id })} />
                ))}
                {page.items.length === 0 && (
                  <tr>
                    <td colSpan={4} className="empty">
                      No entities match. Entities are computed after each analysis run.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        {page && <p className="graph-foot">{fmtInt(page.total)} entities{page.total > page.items.length ? ` (showing ${page.items.length})` : ''}. “Linked wallets” is a read-only cross-reference for investigators; entities themselves are built without reading wallet identity.</p>}
      </Panel>
    </>
  );
}

function EntityRow({ e, active, onOpen }: { e: EntitySummary; active: boolean; onOpen: () => void }) {
  return (
    <tr className={`row-link${active ? ' row-open' : ''}`} onClick={onOpen}>
      <td className="mono">{e.entity_id}</td>
      <td className="num mono">{e.address_count}</td>
      <td>
        {e.linked_wallets.slice(0, 3).map((w) => (
          <Link key={w} to={`/wallets/${w}`} className="mono chip-link" onClick={(ev) => ev.stopPropagation()}>
            {w}
          </Link>
        ))}
        {e.linked_wallets.length > 3 && <span className="muted"> and {e.linked_wallets.length - 3} more</span>}
        {e.linked_wallets.length === 0 && <span className="muted">—</span>}
      </td>
      <td className="num mono">{fmtInt(e.transaction_count)}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------------------------
function EntityPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [d, setD] = useState<AddressEntityDetail | null>(null);
  const [graph, setGraph] = useState<GraphOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const store = useCases();
  const navigate = useNavigate();
  const [title, setTitle] = useState('');
  const [existing, setExisting] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<AddressEntityDetail>(`/api/entities/${encodeURIComponent(id)}`).then(
      (x) => {
        if (!live) return;
        setD(x);
        setError(null);
        setTitle((t) => t || `Review of entity ${x.entity_id}`);
      },
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    api.get<GraphOut>(`/api/entity-graph${qs({ focus: 'entity', id, max_nodes: 60 })}`).then(
      (g) => live && setGraph(g),
      () => live && setGraph(null),
    );
    return () => {
      live = false;
    };
  }, [id]);

  if (error) {
    return (
      <Panel title={id} actions={<button className="btn btn-sm" onClick={onClose}>Close</button>}>
        <p className="field-error">{error}. Entity ids can change when the analysis is re-run.</p>
      </Panel>
    );
  }
  if (!d) return <Panel title={id}><div className="viz-loading">Loading entity…</div></Panel>;

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
  const openCases = store.cases.filter((c) => c.status !== 'Closed');

  return (
    <Panel
      title={
        <>
          <span className="mono">{d.entity_id}</span> · {d.method_label}
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
          <div className="metric-label">Addresses</div>
          <div className="metric-value">{d.address_count}</div>
          <div className="metric-sub">{fmtInt(d.transaction_count)} spending transactions</div>
        </div>
        <div className="metric">
          <div className="metric-label">Entity–IP links</div>
          <div className="metric-value">{d.ip_links.length}</div>
          <div className="metric-sub">{d.distinct_ip_count} distinct IPs · {d.distinct_country_count} countries (synthetic)</div>
        </div>
        <div className="metric">
          <div className="metric-label">Entity–entity links</div>
          <div className="metric-value">{d.links.length}</div>
          <div className="metric-sub">{d.related_entities.length} related entit{d.related_entities.length === 1 ? 'y' : 'ies'}</div>
        </div>
      </div>

      <div className="related-block">
        <h4>Member addresses</h4>
        <ul className="cluster-mini">
          {d.addresses.slice(0, 20).map((a) => (
            <li key={a}>
              <span className="mono">{a}</span>
            </li>
          ))}
          {d.addresses.length > 20 && <li className="muted">and {d.addresses.length - 20} more</li>}
        </ul>
        {d.linked_wallets.length > 0 && (
          <p className="muted">
            Linked wallets (read-only cross-reference, not used to build this entity): {d.linked_wallets.map((w) => (
              <Link key={w} to={`/wallets/${w}`} className="mono chip-link">
                {w}
              </Link>
            ))}
          </p>
        )}
      </div>

      <div className="related-block">
        <h4>
          Correlation findings <span className="rule-tag">synthetic</span>
        </h4>
        {d.findings.length === 0 ? (
          <p className="muted">No correlation finding was recorded for this entity.</p>
        ) : (
          <ul className="reasons">
            {d.findings.map((f, i) => (
              <li key={i}>
                {f.description} <span className="muted">({f.finding_type})</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {graph && (
        <>
          <ApiGraph graph={graph} height={480} />
          <p className="graph-foot">{graphCaption(graph)}.</p>
        </>
      )}

      {store.available && (
        <div className="cluster-actions">
          <div className="add-item">
            <span>New case</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Case title" maxLength={200} />
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={busy || !title.trim()}
              onClick={() => void act(async () => (await store.createCase({ title, items: [{ type: 'entity', id: d.entity_id }] })).case_id)}
            >
              Create case from entity
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
                onClick={() => void act(async () => { await store.addItem(existing, 'entity', d.entity_id); return existing; })}
              >
                Add to case
              </button>
            </div>
          )}
          {actionError && <p className="field-error" role="alert">{actionError}</p>}
        </div>
      )}
      <p className="graph-foot">Entity created {fmtDateTime(d.created_at)} · updated {fmtDateTime(d.updated_at)}.</p>
    </Panel>
  );
}
