import { describe, expect, it, vi } from "vitest";
import { loadStatus } from "./status.js";

describe("loadStatus", () => {
  it("shows the API's status", async () => {
    const setStatus = vi.fn();
    await loadStatus(async () => ({ status: "ok" }), setStatus);
    expect(setStatus).toHaveBeenCalledWith("ok");
  });

  it("shows unavailable when the call fails", async () => {
    const setStatus = vi.fn();
    await loadStatus(async () => { throw new Error("down"); }, setStatus);
    expect(setStatus).toHaveBeenCalledWith("unavailable");
  });
});
