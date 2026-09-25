// Thin client for the local backend (FastAPI). All calls are relative ('/api/...'); Vite proxies them in
// development. Nothing here holds a credential: the backend needs none.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

/** The backend could not be reached at all (as opposed to answering with an error). */
export class Unreachable extends Error {}

async function request<T>(method: string, path: string, body?: unknown, opts: { timeoutMs?: number } = {}): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeoutMs ?? 60_000);
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
  } catch (e) {
    throw new Unreachable(e instanceof Error ? e.message : String(e));
  } finally {
    clearTimeout(timer);
  }
  // The dev proxy answers 502/503/504 (empty body) when the backend is not listening.
  if (res.status === 502 || res.status === 503 || res.status === 504) throw new Unreachable(`Backend answered ${res.status}`);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    // Not JSON: the dev proxy answers with plain text when the backend is down.
    if (res.status >= 500) throw new Unreachable(`Backend answered ${res.status}`);
  }
  if (!res.ok) {
    const err = (json as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? 'error', err?.message ?? `Request failed (${res.status})`, err?.details);
  }
  return json as T;
}

export const api = {
  get: <T>(path: string, opts?: { timeoutMs?: number }) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: { timeoutMs?: number }) => request<T>('POST', path, body ?? {}, opts),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  del: <T = void>(path: string) => request<T>('DELETE', path),
};

/** Builds '?a=1&b=2', skipping empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : '';
}
