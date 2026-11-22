import type { JobResult } from "@/lib/api";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-IN").format(n);
}

/** `posted today` / `posted 2d ago` / `posted 2mo ago`; falls back to first_seen_at. */
export function formatPosted(
  job: Pick<JobResult, "posted_at" | "first_seen_at">,
  now: Date = new Date(),
): string {
  const when = new Date(job.posted_at ?? job.first_seen_at).getTime();
  const age = Math.max(0, now.getTime() - when);
  if (age < DAY) return "posted today";
  const days = Math.floor(age / DAY);
  if (days < 60) return `posted ${days}d ago`;
  return `posted ${Math.floor(days / 30)}mo ago`;
}

/** `checked just now` / `checked 45m ago` / `checked 5h ago` / `checked 2d ago`. */
export function formatChecked(
  lastSeenAt: string,
  now: Date = new Date(),
): string {
  const age = Math.max(0, now.getTime() - new Date(lastSeenAt).getTime());
  if (age < MINUTE) return "checked just now";
  if (age < HOUR) return `checked ${Math.floor(age / MINUTE)}m ago`;
  if (age < 48 * HOUR) return `checked ${Math.floor(age / HOUR)}h ago`;
  return `checked ${Math.floor(age / DAY)}d ago`;
}

function lpa(n: number): string {
  return String(Math.round(n * 10) / 10);
}

export type SalaryTag = {
  text: string;
  style: "neutral" | "outline";
  estimate: boolean;
};

export function formatSalary(
  job: Pick<
    JobResult,
    "salary_min_lpa" | "salary_max_lpa" | "salary_is_estimate"
  >,
): SalaryTag {
  const lo = job.salary_min_lpa ?? job.salary_max_lpa;
  const hi = job.salary_max_lpa ?? job.salary_min_lpa;
  if (lo == null || hi == null) {
    return { text: "Salary not stated", style: "outline", estimate: false };
  }
  const body =
    lpa(lo) === lpa(hi) ? `₹${lpa(lo)} LPA` : `₹${lpa(lo)}–${lpa(hi)} LPA`;
  if (job.salary_is_estimate) {
    return {
      text: `~ ${body} estimate`,
      style: "outline",
      estimate: true,
    };
  }
  return { text: body, style: "neutral", estimate: false };
}

export type RegionTag = {
  text: string;
  style: "green" | "outline" | "neutral";
  icon: "Globe" | "CircleHelp" | "Building2" | null;
};

export function regionTag(
  job: Pick<
    JobResult,
    "remote_type" | "remote_eligibility" | "onsite_days_per_week"
  >,
): RegionTag | null {
  switch (job.remote_type) {
    case "remote":
      switch (job.remote_eligibility) {
        case "worldwide":
        case "india":
        case "apac":
          return { text: "Remote · India OK", style: "green", icon: "Globe" };
        case "us_only":
          return { text: "Remote · US only", style: "outline", icon: "Globe" };
        case "eu_only":
          return { text: "Remote · EU only", style: "outline", icon: "Globe" };
        case "other_region":
          return {
            text: "Remote · Other region",
            style: "outline",
            icon: "Globe",
          };
        default:
          return {
            text: "Remote · Region not stated",
            style: "outline",
            icon: "CircleHelp",
          };
      }
    case "hybrid":
      return {
        text:
          job.onsite_days_per_week != null
            ? `Hybrid · ${job.onsite_days_per_week} days on-site`
            : "Hybrid",
        style: "neutral",
        icon: "Building2",
      };
    case "onsite":
      return { text: "On-site", style: "neutral", icon: "Building2" };
    default:
      return null;
  }
}

const STALE_SENTENCES: Record<JobResult["ghost_reasons"][number], string> = {
  stale_60d: "First seen over 60 days ago and still listed.",
  reposted_3x: "Reposted 3 or more times.",
  no_apply_url: "No direct link to apply.",
};

export function staleReasons(reasons: JobResult["ghost_reasons"]): string[] {
  return reasons.map((r) => STALE_SENTENCES[r]);
}

/** Short place text for the "company · place" row. */
export function placeText(
  job: Pick<JobResult, "city" | "cities" | "remote_type">,
): string {
  const cities =
    job.cities.length > 0 ? job.cities : job.city ? [job.city] : [];
  if (cities.length === 0)
    return job.remote_type === "remote" ? "Remote" : "Location not stated";
  if (cities.length === 1) return cities[0];
  const shown = cities.slice(0, 2).join(", ");
  const extra = cities.length - 2;
  return `Multiple cities: ${shown}${extra > 0 ? ` +${extra}` : ""}`;
}
