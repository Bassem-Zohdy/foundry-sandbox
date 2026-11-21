import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchHealth } from "./health.js";

afterEach(() => vi.unstubAllGlobals());

describe("fetchHealth", () => {
  it("returns the API's health body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 })));
    await expect(fetchHealth()).resolves.toEqual({ status: "ok" });
  });

  it("rejects a body of the wrong shape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ up: true }), { status: 200 })));
    await expect(fetchHealth()).rejects.toThrow();
  });
});
