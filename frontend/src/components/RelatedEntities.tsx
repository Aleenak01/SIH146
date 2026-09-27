import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, qs } from '../api/client';
import type { ClusterPage, ClusterSummary, EntityPage, EntitySummary, GraphOut, ObservationPage, PeelingChainDetail, PeelingChainPage, PeelingChainSummary } from '../api/types';
import type { WalletRecord } from '../data/types';
import { fmtBtc, fmtInt } from '../format';
import { ApiGraph, graphCaption } from '../graph/ApiGraph';
import { Panel } from './bits';

/**
 * What else the backend connects a wallet to: the clusters it belongs to, and the SYNTHETIC IP addresses and devices
 * observed with its transactions. None of this is blockchain data: the observations are generated for the demo.
 */
export function RelatedEntities({ wallet }: { wallet: WalletRecord }) {
  const id = wallet.id;
  const [clusters, setClusters] = useState<ClusterSummary[] | null>(null);
  const [obs, setObs] = useState<ObservationPage | null>(null);
  const [addressEntities, setAddressEntities] = useState<EntitySummary[] | null>(null);
  const [chains, setChains] = useState<PeelingChainSummary[] | null>(null);
  const [graph, setGraph] = useState<GraphOut | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setClusters(null);
    setObs(null);
    setAddressEntities(null);
    setChains(null);
    setGraph(null);
    setShowGraph(false);
    Promise.all([
      api.get<ClusterPage>(`/api/clusters${qs({ q: id, limit: 20 })}`),
      api.get<ObservationPage>(`/api/network/observations${qs({ wallet: id, limit: 500 })}`),
      api.get<EntityPage>(`/api/entities${qs({ wallet: id, limit: 20 })}`),
      api.get<PeelingChainPage>(`/api/peeling-chains${qs({ wallet: id, limit: 20 })}`),
    ]).then(
      ([c, o, e, p]) => {
        if (!live) return;
        setClusters(c.items);
        setObs(o);
        setAddressEntities(e.items);
        setChains(p.items);
      },
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    if (!showGraph) return;
    let live = true;
    api.get<GraphOut>(`/api/graph${qs({ wallet: id, depth: 1, node_types: 'wallet,device,ip_observation', max_nodes: 20 })}`).then(
      (g) => live && setGraph(g),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [showGraph, id]);

  const entities = useMemo(() => {
    const count = (pick: (o: ObservationPage['items'][number]) => string | null) => {
      const m = new Map<string, number>();
      for (const o of obs?.items ?? []) {
        const k = pick(o);
        if (k) m.set(k, (m.get(k) ?? 0) + 1);
      }
      return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    };
    return { devices: count((o) => o.device_id), ips: count((o) => o.ip_address) };
  }, [obs]);

  return (
    <Panel
      title="Related entities"
      note="Clusters this wallet belongs to, and the synthetic network observations recorded with its transactions. A cluster indicates connected activity, not common ownership or wrongdoing."
    >
      {error && <p className="field-error">Could not load related entities: {error}</p>}
      {wallet.lead?.isLead && wallet.lead.reasons.length > 0 && (
        <div className="related-block">
          <h4>Why this wallet is a lead</h4>
          <ul className="reasons">
            {wallet.lead.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="related-grid">
        <div className="related-block">
          <h4>Clusters</h4>
          {!clusters ? (
            <p className="muted">Loading…</p>
          ) : clusters.length === 0 ? (
            <p className="muted">This wallet is not part of any cluster.</p>
          ) : (
            <ul className="cluster-mini">
              {clusters.map((c) => (
                <li key={c.cluster_id}>
                  <Link to={`/network?view=clusters&cluster=${c.cluster_id}`} className="mono">
                    {c.cluster_id}
                  </Link>{' '}
                  <span className="muted">
                    · {c.method_label} · {c.wallet_count} wallets · {c.priority_summary?.lead_count ?? 0} leads
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="related-block">
          <h4>
            Network observations <span className="rule-tag">synthetic</span>
          </h4>
          {!obs ? (
            <p className="muted">Loading…</p>
          ) : obs.total === 0 ? (
            <p className="muted">No network observation was recorded for this wallet.</p>
          ) : (
            <>
              <p className="muted">
                {fmtInt(obs.total)} observations · {entities.devices.length} device{entities.devices.length === 1 ? '' : 's'} · {entities.ips.length} IP address{entities.ips.length === 1 ? '' : 'es'}
              </p>
              <ul className="cluster-mini">
                {entities.devices.slice(0, 6).map(([d, n]) => (
                  <li key={d}>
                    Device{' '}
                    <Link to={`/network?view=clusters&entity=device:${d}`} className="mono">
                      {d}
                    </Link>{' '}
                    <span className="muted">· {n} observations</span>
                  </li>
                ))}
                {entities.ips.slice(0, 6).map(([ip, n]) => (
                  <li key={ip}>
                    IP{' '}
                    <Link to={`/network?view=clusters&entity=ip:${ip}`} className="mono">
                      {ip}
                    </Link>{' '}
                    <span className="muted">· {n} observations</span>
                  </li>
                ))}
              </ul>
              <p className="caveat">{obs.note}</p>
            </>
          )}
        </div>

        <div className="related-block">
          <h4>Address entity</h4>
          {!addressEntities ? (
            <p className="muted">Loading…</p>
          ) : addressEntities.length === 0 ? (
            <p className="muted">This wallet has no address entity from the common-input-ownership heuristic.</p>
          ) : (
            <ul className="cluster-mini">
              {addressEntities.map((e) => (
                <li key={e.entity_id}>
                  <Link to={`/network?view=entities&entity=${e.entity_id}`} className="mono">
                    {e.entity_id}
                  </Link>{' '}
                  <span className="muted">
                    · {e.address_count} addresses · {e.transaction_count} transactions
                  </span>
                </li>
              ))}
              <li className="caveat">Ownership inference — a heuristic, not proof.</li>
            </ul>
          )}
        </div>

        <div className="related-block">
          <h4>Peeling chain membership</h4>
          {!chains ? (
            <p className="muted">Loading…</p>
          ) : chains.length === 0 ? (
            <p className="muted">This wallet does not appear in any detected peeling chain.</p>
          ) : (
            <ul className="cluster-mini">
              {chains.map((c) => (
                <PeelingChainItem key={c.chain_id} chain={c} wallet={id} />
              ))}
              <li className="caveat">A heuristic pattern (large, repeated change) — an investigative signal, not proof of layering.</li>
            </ul>
          )}
        </div>
      </div>

      <div className="related-block">
        {!showGraph ? (
          <button type="button" className="btn btn-sm" onClick={() => setShowGraph(true)}>
            Show relationship graph with network infrastructure
          </button>
        ) : graph ? (
          <>
            <ApiGraph graph={graph} height={520} />
            <p className="graph-foot">
              {graphCaption(graph)}. Dashed lines to IP addresses and devices are SYNTHETIC network observations generated for the demo, not blockchain data.
            </p>
          </>
        ) : (
          <div className="viz-loading">Loading graph…</div>
        )}
      </div>
    </Panel>
  );
}

/** One peeling-chain row: the chain id, this wallet's hop position, and an expandable hop sequence. */
function PeelingChainItem({ chain, wallet }: { chain: PeelingChainSummary; wallet: string }) {
  const [detail, setDetail] = useState<PeelingChainDetail | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || detail) return;
    let live = true;
    api.get<PeelingChainDetail>(`/api/peeling-chains/${encodeURIComponent(chain.chain_id)}`).then(
      (d) => live && setDetail(d),
      () => live && setDetail(null),
    );
    return () => {
      live = false;
    };
  }, [open, detail, chain.chain_id]);

  const hopPosition = detail?.hops.find((h) => h.from_wallet === wallet || h.to_wallet === wallet)?.hop_index;

  return (
    <li>
      <button type="button" className="mono chip-link" onClick={() => setOpen((v) => !v)}>
        {chain.chain_id}
      </button>{' '}
      <span className="muted">
        · {chain.hop_count} hops · {fmtBtc(chain.total_btc_start)} → {fmtBtc(chain.total_btc_end)} BTC
        {hopPosition !== undefined ? ` · this wallet at hop ${hopPosition}` : ''}
      </span>
      {open && (
        <div className="table-wrap">
          {!detail ? (
            <p className="muted">Loading hop sequence…</p>
          ) : (
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Hop</th>
                  <th>From</th>
                  <th>To</th>
                  <th className="num">BTC</th>
                </tr>
              </thead>
              <tbody>
                {detail.hops.map((h) => (
                  <tr key={h.hop_index} className={h.from_wallet === wallet || h.to_wallet === wallet ? 'row-open' : ''}>
                    <td className="mono">{h.hop_index}</td>
                    <td>
                      <Link to={`/wallets/${h.from_wallet}`} className="mono wallet-link">
                        {h.from_wallet}
                      </Link>
                    </td>
                    <td>
                      <Link to={`/wallets/${h.to_wallet}`} className="mono wallet-link">
                        {h.to_wallet}
                      </Link>
                    </td>
                    <td className="num mono">{fmtBtc(h.amount_btc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </li>
  );
}
