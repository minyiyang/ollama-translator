import { describe, expect, it, vi } from "vitest";
import { api, ApiError, uploadSource } from "./api";
import { setLocale } from "./i18n";

/** The server answers every request with this, after handing out a token. */
function serverSays(status: number, body: unknown, statusText = "") {
  vi.stubGlobal("fetch", vi.fn(async (path: string) =>
    path === "/api/session" ? new Response(JSON.stringify({ token: "t" })) : new Response(JSON.stringify(body), { status, statusText })));
}

describe("A request the server refuses", () => {
  const refusal = { error: "a job named a1 already exists", code: "job_exists", params: { job: "a1" } };

  it("fails with the server's reason in the interface language", async () => {
    serverSays(422, refusal);
    await setLocale("de", false);
    await expect(api("/api/jobs/new", {})).rejects.toThrow(new ApiError("Ein Job namens a1 existiert bereits"));
  });

  it("fails with the server's own text when the reason has no code", async () => {
    serverSays(422, { error: "boom" });
    await setLocale("de", false);
    await expect(api("/api/setup")).rejects.toThrow("boom");
  });

  it("falls back to the status when the server gives no reason", async () => {
    serverSays(500, {}, "Internal Server Error");
    await expect(api("/api/setup")).rejects.toThrow("Internal Server Error");
  });

  it("says why an upload was refused, the same way", async () => {
    serverSays(413, { error: "the file is empty or larger than 1 GB", code: "upload_size", params: {} });
    await setLocale("fr", false);
    await expect(uploadSource(new File(["x"], "a.epub"))).rejects.toThrow("le fichier est vide ou dépasse 1 Go");
  });
});
