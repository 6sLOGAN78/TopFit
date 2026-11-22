import { describe, expect, it } from "vitest";
import fixture from "./__fixtures__/search-response.json";
import { zSearchJobsApiJobsSearchGetResponse } from "./api/zod.gen";

describe("generated zod contract", () => {
  it("parses a FastAPI-serialised search response (Z and +05:30 timestamps, null posted_at)", () => {
    const parsed = zSearchJobsApiJobsSearchGetResponse.parse(fixture);
    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[1].posted_at).toBeNull();
  });

  it("rejects a response with an unknown enum value", () => {
    const bad = structuredClone(fixture) as unknown as {
      items: { remote_type: string }[];
    };
    bad.items[0].remote_type = "moon";
    expect(() => zSearchJobsApiJobsSearchGetResponse.parse(bad)).toThrow();
  });
});
