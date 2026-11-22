import type { SearchJobsApiJobsSearchGetData } from "@/lib/api";

type SearchQuery = SearchJobsApiJobsSearchGetData["query"];
export type WorkMode = NonNullable<SearchQuery["mode"]>[number];
export type SeniorityValue = NonNullable<SearchQuery["seniority"]>[number];
export type ParseModel = NonNullable<SearchQuery["parse_model"]>;

const MODES: readonly WorkMode[] = ["remote", "hybrid", "onsite"];
const SENIORITIES: readonly SeniorityValue[] = [
  "intern",
  "junior",
  "mid",
  "senior",
  "lead",
];

export type SearchUrlState = {
  q: string;
  city: string[];
  remote_ok: boolean;
  min_lpa: number | null;
  no_est: boolean;
  mode: WorkMode[];
  seniority: SeniorityValue[];
  auto: boolean;
};

function flag(v: string | null): boolean {
  return v === "1" || v === "true";
}

export function readSearchParams(sp: URLSearchParams): SearchUrlState {
  const minRaw = sp.get("min_lpa");
  const min = minRaw === null || minRaw === "" ? NaN : Number(minRaw);
  return {
    q: sp.get("q") ?? "",
    city: sp.getAll("city").filter((c) => c.trim() !== ""),
    remote_ok: flag(sp.get("remote_ok")),
    min_lpa: Number.isFinite(min) && min >= 0 ? min : null,
    no_est: flag(sp.get("no_est")),
    mode: sp
      .getAll("mode")
      .filter((m): m is WorkMode => (MODES as readonly string[]).includes(m)),
    seniority: sp
      .getAll("seniority")
      .filter((s): s is SeniorityValue =>
        (SENIORITIES as readonly string[]).includes(s),
      ),
    auto: sp.get("auto") !== "0" && sp.get("auto") !== "false",
  };
}

/** Writes only non-default values. The parse model is a per-browser setting and never goes in the URL. */
export function writeSearchParams(state: SearchUrlState): URLSearchParams {
  const sp = new URLSearchParams();
  if (state.q) sp.set("q", state.q);
  for (const c of state.city) sp.append("city", c);
  if (state.remote_ok) sp.set("remote_ok", "1");
  if (state.min_lpa !== null) sp.set("min_lpa", String(state.min_lpa));
  if (state.no_est) sp.set("no_est", "1");
  for (const m of state.mode) sp.append("mode", m);
  for (const s of state.seniority) sp.append("seniority", s);
  if (!state.auto) sp.set("auto", "0");
  return sp;
}

export function toSearchQuery(
  state: SearchUrlState,
  parseModel: ParseModel,
  cursor?: string | null,
): SearchQuery {
  const query: SearchQuery = {
    q: state.q,
    auto: state.auto,
    parse_model: parseModel,
  };
  if (state.city.length > 0) query.city = state.city;
  if (state.remote_ok) query.remote_ok = true;
  if (state.min_lpa !== null) query.min_lpa = state.min_lpa;
  if (state.no_est) query.no_est = true;
  if (state.mode.length > 0) query.mode = state.mode;
  if (state.seniority.length > 0) query.seniority = state.seniority;
  if (cursor) query.cursor = cursor;
  return query;
}
