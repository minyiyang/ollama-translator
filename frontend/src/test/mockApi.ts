import { vi } from "vitest";

type Call = { method: string; path: string; body: unknown };
type Handler = unknown | ((body: any, url: URL) => unknown);

export const jsonResponse = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

/** An API failure as the server reports one: `{ error }` with a non-2xx status. */
export const apiError = (message: string, status = 500) => jsonResponse({ error: message }, status);

/** A response the test releases by hand, for asserting what shows while a request is in flight. */
export function deferred<T = unknown>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

/**
 * Stub `fetch` for the dashboard API. Routes are keyed "METHOD /api/path" (no
 * query string); a handler is a payload, a Response, or a function of the parsed
 * body and URL. Anything unrouted answers 404 so a missing mock shows up as an
 * error on the page instead of hanging.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path: `${url.pathname}${url.search}`, body });
    if (url.pathname === "/api/session") return jsonResponse({ token: "test-token" });
    const key = `${method} ${url.pathname}`;
    if (!(key in routes)) return jsonResponse({ error: `unmocked ${key}` }, 404);
    const route = routes[key];
    const result = typeof route === "function" ? await route(body, url) : route;
    return result instanceof Response ? result : jsonResponse(result);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    /** Bodies posted to one API path, oldest first. */
    posted: (path: string) => calls.filter((c) => c.method === "POST" && c.path === path).map((c) => c.body),
    requested: (path: string) => calls.some((c) => c.path === path),
    /** How many times one path (query string included) was requested, by any method. */
    count: (path: string) => calls.filter((c) => c.path === path).length,
  };
}
