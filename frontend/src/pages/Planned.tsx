import { PageHeader, Panel } from '../components/bits';

export function Planned({ title, lead, items }: { title: string; lead: string; items: string[] }) {
  return (
    <div className="page">
      <PageHeader title={title} subtitle={lead} />
      {items.length > 0 && (
        <Panel title="Planned">
          <ul className="plain-list">
            {items.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
