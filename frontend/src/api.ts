// All requests go to the local book-agent server. POSTs carry the per-server
// token, fetched from the same origin (cross-site pages cannot read it). A
// restarted server issues a new token, so a 403 refreshes it and retries once.
let tokenPromise: Promise<string> | null = null;

export function token(refresh = false): Promise<string> {
  if (refresh) tokenPromise = null;
  tokenPromise ??= fetch("/api/session")
    .then((r) => r.json())
    .then((j: { token: string }) => j.token);
  return tokenPromise;
}

async function postWithToken(path: string, init: RequestInit): Promise<Response> {
  const send = async (fresh: boolean) =>
    fetch(path, { ...init, method: "POST", headers: { ...init.headers, "X-UI-Token": await token(fresh) } });
  const response = await send(false);
  return response.status === 403 ? send(true) : response;
}

export class ApiError extends Error {}

export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const response =
    body === undefined
      ? await fetch(path)
      : await postWithToken(path, { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const json = await response.json();
  if (!response.ok) throw new ApiError(json.error || response.statusText);
  return json as T;
}

export const jobApi = <T = any>(jobId: string, path: string, body?: unknown) =>
  api<T>(`/api/jobs/${encodeURIComponent(jobId)}/${path}`, body);

/** Send a picked or dropped book to the server; returns its saved path. */
export async function uploadSource(file: File): Promise<string> {
  const response = await postWithToken(`/api/uploads?name=${encodeURIComponent(file.name)}`, {
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  const json = await response.json();
  if (!response.ok) throw new ApiError(json.error || response.statusText);
  return json.path as string;
}
