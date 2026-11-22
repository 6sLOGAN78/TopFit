import { describe, expect, it } from "vitest";
import {
  formatChecked,
  formatCount,
  formatPosted,
  formatSalary,
  regionTag,
  staleReasons,
} from "./format";

const NOW = new Date("2026-11-22T12:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
const H = 3_600_000;
const D = 24 * H;
const M = 60_000;

describe("formatPosted", () => {
  it("says posted today under 24h", () => {
    expect(
      formatPosted({ posted_at: ago(5 * H), first_seen_at: ago(D) }, NOW),
    ).toBe("posted today");
  });
  it("uses days", () => {
    expect(
      formatPosted({ posted_at: ago(2 * D), first_seen_at: ago(D) }, NOW),
    ).toBe("posted 2d ago");
    expect(
      formatPosted({ posted_at: ago(59 * D), first_seen_at: ago(D) }, NOW),
    ).toBe("posted 59d ago");
  });
  it("uses months from 60 days", () => {
    expect(
      formatPosted({ posted_at: ago(75 * D), first_seen_at: ago(D) }, NOW),
    ).toBe("posted 2mo ago");
  });
  it("falls back to first_seen_at when posted_at is null", () => {
    expect(
      formatPosted({ posted_at: null, first_seen_at: ago(3 * D) }, NOW),
    ).toBe("posted 3d ago");
  });
});

describe("formatChecked", () => {
  it("handles minutes, hours and days", () => {
    expect(formatChecked(ago(0), NOW)).toBe("checked just now");
    expect(formatChecked(ago(45 * M), NOW)).toBe("checked 45m ago");
    expect(formatChecked(ago(5 * H), NOW)).toBe("checked 5h ago");
    expect(formatChecked(ago(50 * H), NOW)).toBe("checked 2d ago");
  });
});

describe("formatSalary", () => {
  const base = { salary_is_estimate: false };
  it("formats a range with an en dash", () => {
    expect(
      formatSalary({ ...base, salary_min_lpa: 12, salary_max_lpa: 18 }),
    ).toMatchObject({ text: "₹12–18 LPA", style: "neutral" });
  });
  it("formats a single value", () => {
    expect(
      formatSalary({ ...base, salary_min_lpa: 15, salary_max_lpa: 15 }).text,
    ).toBe("₹15 LPA");
  });
  it("keeps one decimal and drops trailing .0", () => {
    expect(
      formatSalary({ ...base, salary_min_lpa: 4.5, salary_max_lpa: 4.5 }).text,
    ).toBe("₹4.5 LPA");
    expect(
      formatSalary({ ...base, salary_min_lpa: 12.0, salary_max_lpa: 18.04 })
        .text,
    ).toBe("₹12–18 LPA");
  });
  it("marks estimates", () => {
    expect(
      formatSalary({
        salary_is_estimate: true,
        salary_min_lpa: 12,
        salary_max_lpa: 18,
      }),
    ).toMatchObject({
      text: "~ ₹12–18 LPA estimate",
      style: "outline",
      estimate: true,
    });
  });
  it("handles unknown salary", () => {
    expect(
      formatSalary({
        salary_is_estimate: false,
        salary_min_lpa: null,
        salary_max_lpa: null,
      }),
    ).toMatchObject({ text: "Salary not stated", style: "outline" });
  });
  it("handles a one-sided salary", () => {
    expect(
      formatSalary({ ...base, salary_min_lpa: 10, salary_max_lpa: null }).text,
    ).toBe("₹10 LPA");
  });
});

describe("regionTag", () => {
  const job = (
    remote_type: string,
    remote_eligibility: string,
    onsite_days_per_week: number | null = null,
  ) => ({ remote_type, remote_eligibility, onsite_days_per_week }) as never;

  it("maps remote regions", () => {
    for (const r of ["india", "apac", "worldwide"]) {
      expect(regionTag(job("remote", r))).toEqual({
        text: "Remote · India OK",
        style: "green",
        icon: "Globe",
      });
    }
    expect(regionTag(job("remote", "unknown"))).toMatchObject({
      text: "Remote · Region not stated",
      style: "outline",
    });
    expect(regionTag(job("remote", "us_only"))?.text).toBe("Remote · US only");
    expect(regionTag(job("remote", "eu_only"))?.text).toBe("Remote · EU only");
    expect(regionTag(job("remote", "other_region"))?.text).toBe(
      "Remote · Other region",
    );
  });
  it("maps hybrid, onsite and unknown", () => {
    expect(regionTag(job("hybrid", "unknown", 3))?.text).toBe(
      "Hybrid · 3 days on-site",
    );
    expect(regionTag(job("hybrid", "unknown"))?.text).toBe("Hybrid");
    expect(regionTag(job("onsite", "unknown"))?.text).toBe("On-site");
    expect(regionTag(job("unknown", "unknown"))).toBeNull();
  });
});

describe("staleReasons and formatCount", () => {
  it("maps ghost reasons to sentences", () => {
    expect(staleReasons(["stale_60d", "reposted_3x", "no_apply_url"])).toEqual([
      "First seen over 60 days ago and still listed.",
      "Reposted 3 or more times.",
      "No direct link to apply.",
    ]);
  });
  it("formats counts en-IN", () => {
    expect(formatCount(12408)).toBe("12,408");
    expect(formatCount(1234567)).toBe("12,34,567");
  });
});
