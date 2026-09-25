import { useBackend } from '../state/data';

/** Says which data source the screens are using, and only when it is not the normal connected state. */
export function ConnectionBanner() {
  const b = useBackend();
  if (b.mode === 'connecting') return null;

  if (b.mode === 'api') {
    if (b.online) return null;
    return (
      <div className="banner banner-warn" role="status">
        Lost contact with the local backend. The data below is the last that was loaded; it will refresh when the backend answers again.
      </div>
    );
  }

  return (
    <div className="banner" role="status">
      <div>
        <b>Offline mode</b> — showing the pipeline output bundled with the app (read-only). {b.reason}{' '}
        {b.emptyBackend
          ? 'Load the synthetic demo data to use leads, clusters, cases and monitoring.'
          : 'Leads, clusters, network observations, cases and monitoring need the backend: run “python -m backend” in the project folder.'}
      </div>
      <div className="banner-actions">
        {b.emptyBackend && (
          <button type="button" className="btn btn-sm btn-primary" onClick={() => void b.loadDemoData()} disabled={!!b.busy}>
            Load synthetic demo data
          </button>
        )}
        <button type="button" className="btn btn-sm" onClick={() => void b.reconnect()} disabled={!!b.busy}>
          Retry connection
        </button>
      </div>
      {b.busy && <div className="banner-busy">{b.busy}</div>}
    </div>
  );
}
