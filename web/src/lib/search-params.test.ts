import { describe, expect, it } from "vitest";
import {
  readSearchParams,
  toSearchQuery,
  writeSearchParams,
} from "./search-params";

describe("readSearchParams", () => {
  it("reads every param", () => {
    expect(
      readSearchParams(
        new URLSearchParams(
          "q=x&city=Pune&city=Bengaluru&min_lpa=10&no_est=1&mode=remote&auto=0",
        ),
      ),
    ).toEqual({
      q: "x",
      city: ["Pune", "Bengaluru"],
      min_lpa: 10,
      no_est: true,
      mode: ["remote"],
      auto: false,
      remote_ok: false,
      seniority: [],
    });
  });
  it("applies defaults", () => {
    expect(readSearchParams(new URLSearchParams())).toEqual({
      q: "",
      city: [],
      min_lpa: null,
      no_est: false,
      mode: [],
      auto: true,
      remote_ok: false,
      seniority: [],
    });
  });
  it("drops unknown enum values and bad numbers", () => {
    const s = readSearchParams(
      new URLSearchParams(
        "q=x&mode=moon&mode=hybrid&seniority=god&min_lpa=abc",
      ),
    );
    expect(s.mode).toEqual(["hybrid"]);
    expect(s.seniority).toEqual([]);
    expect(s.min_lpa).toBeNull();
  });
});

describe("writeSearchParams", () => {
  it("round-trips", () => {
    const sp = new URLSearchParams(
      "q=backend&city=Pune&remote_ok=1&min_lpa=12&no_est=1&mode=remote&mode=hybrid&seniority=senior&auto=0",
    );
    const state = readSearchParams(sp);
    expect(readSearchParams(writeSearchParams(state))).toEqual(state);
  });
  it("omits defaults and never writes parse_model", () => {
    const out = writeSearchParams(readSearchParams(new URLSearchParams("q=a")));
    expect(out.toString()).toBe("q=a");
    expect(out.has("parse_model")).toBe(false);
  });
});

describe("toSearchQuery", () => {
  it("maps state to API query and adds parse_model and cursor", () => {
    const state = readSearchParams(
      new URLSearchParams("q=py&city=Pune&min_lpa=10&auto=0"),
    );
    expect(toSearchQuery(state, "haiku", "cur")).toMatchObject({
      q: "py",
      city: ["Pune"],
      min_lpa: 10,
      auto: false,
      parse_model: "haiku",
      cursor: "cur",
    });
  });
  it("omits empty filters", () => {
    const q = toSearchQuery(
      readSearchParams(new URLSearchParams("q=py")),
      "jev",
    );
    expect(q.city).toBeUndefined();
    expect(q.cursor).toBeUndefined();
    expect(q.parse_model).toBe("jev");
  });
});
