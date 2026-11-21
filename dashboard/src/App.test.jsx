import { describe, expect, it } from "vitest";
import { z } from "zod";
import { parseResponse } from "./foundry/api";

describe("health response", () => {
  it("accepts the API's health body", async () => {
    const response = new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    await expect(parseResponse(z.object({ status: z.string() }), response)).resolves.toEqual({ status: "ok" });
  });
});
