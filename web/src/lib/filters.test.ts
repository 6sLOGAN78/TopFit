import { describe, expect, it } from "vitest";
import {
  applyChipEdit,
  applyRelax,
  relaxItems,
  relaxLabel,
  removeChip,
  setAsideText,
  stateFromFilters,
} from "./filters";

const filters = {
  cities: ["Bengaluru"],
  remote_ok: true,
  min_salary_lpa: 10,
  seniority: ["senior" as const],
};
const base = stateFromFilters("backend python", filters);

describe("chip transforms", () => {
  it("stateFromFilters makes explicit, auto-off state", () => {
    expect(base).toMatchObject({
      q: "backend python",
      city: ["Bengaluru"],
      remote_ok: true,
      min_lpa: 10,
      auto: false,
    });
  });

  it("removeChip drops only that field", () => {
    const next = removeChip(
      stateFromFilters("q", {
        cities: ["Bengaluru"],
        remote_ok: true,
        min_salary_lpa: 10,
      }),
      "min_salary",
    );
    expect(next.city).toEqual(["Bengaluru"]);
    expect(next.remote_ok).toBe(true);
    expect(next.min_lpa).toBeNull();
    expect(next.auto).toBe(false);
  });

  it("applyChipEdit sets one field and keeps the rest", () => {
    const next = applyChipEdit(base, {
      field: "min_salary",
      min_lpa: 15,
      exclude_estimates: true,
    });
    expect(next).toMatchObject({
      min_lpa: 15,
      no_est: true,
      city: ["Bengaluru"],
      seniority: ["senior"],
      auto: false,
    });
  });

  it("applyChipEdit city keeps remote_ok only with cities", () => {
    expect(
      applyChipEdit(base, { field: "city", cities: ["Pune"], remote_ok: true }),
    ).toMatchObject({ city: ["Pune"], remote_ok: true });
    expect(
      applyChipEdit(base, { field: "city", cities: [], remote_ok: true })
        .remote_ok,
    ).toBe(false);
  });

  it("applyRelax relaxes a single filter", () => {
    const full = stateFromFilters("q", {
      cities: ["Pune"],
      remote_ok: true,
      min_salary_lpa: 10,
      exclude_estimates: true,
      seniority: ["senior"],
      work_modes: ["remote"],
    });
    expect(applyRelax(full, "min_salary")).toMatchObject({
      min_lpa: null,
      city: ["Pune"],
    });
    expect(applyRelax(full, "city")).toMatchObject({
      city: [],
      remote_ok: false,
      min_lpa: 10,
    });
    expect(applyRelax(full, "estimates")).toMatchObject({
      no_est: false,
      min_lpa: 10,
    });
    expect(applyRelax(full, "seniority").seniority).toEqual([]);
    expect(applyRelax(full, "work_mode").mode).toEqual([]);
    expect(applyRelax(full, "work_mode").auto).toBe(false);
  });
});

describe("wording", () => {
  it("setAsideText joins counts", () => {
    expect(
      setAsideText(
        200,
        [
          { filter: "min_salary", count: 34 },
          { filter: "city", count: 12 },
          { filter: "seniority", count: 8 },
          { filter: "geo", count: 18 },
        ],
        { cities: ["Bengaluru"], min_salary_lpa: 10, seniority: ["senior"] },
      ),
    ).toBe(
      "Of the 200 most relevant jobs, set aside: 34 below ₹10 LPA · 12 outside Bengaluru · 8 not senior · 18 US/EU-only remote",
    );
  });

  it("setAsideText is null when everything is zero", () => {
    expect(
      setAsideText(
        200,
        [
          { filter: "geo", count: 0 },
          { filter: "city", count: 0 },
        ],
        filters,
      ),
    ).toBeNull();
  });

  it("relaxLabel pluralises", () => {
    expect(relaxLabel({ filter: "min_salary", count: 34 }, filters)).toBe(
      "Remove ₹10 LPA minimum (+34 jobs)",
    );
    expect(relaxLabel({ filter: "city", count: 1 }, filters)).toBe(
      "Include any city (+1 job)",
    );
  });

  it("relaxItems sorts desc, skips zero and geo, caps at 4", () => {
    const items = relaxItems(
      [
        { filter: "city", count: 12 },
        { filter: "geo", count: 99 },
        { filter: "min_salary", count: 34 },
        { filter: "seniority", count: 8 },
        { filter: "work_mode", count: 5 },
        { filter: "estimates", count: 6 },
        { filter: "city", count: 0 },
      ],
      filters,
    );
    expect(items.map((i) => i.filter)).toEqual([
      "min_salary",
      "city",
      "seniority",
      "estimates",
    ]);
  });
});
