import type { SearchFilters } from "@/lib/api";
import type { SearchUrlState, SeniorityValue, WorkMode } from "./search-params";

export const SALARY_BUCKETS = [
  3, 4, 5, 6, 8, 10, 12, 15, 18, 20, 25, 30, 40, 50,
] as const;

export type FilterField = "city" | "min_salary" | "work_mode" | "seniority";
export const FILTER_FIELDS: readonly FilterField[] = [
  "city",
  "min_salary",
  "work_mode",
  "seniority",
];

export const FIELD_LABEL: Record<FilterField, string> = {
  city: "City",
  min_salary: "Min salary",
  work_mode: "Work mode",
  seniority: "Seniority",
};

export const WORK_MODE_LABEL: Record<WorkMode, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  onsite: "On-site",
};

export const SENIORITY_LABEL: Record<SeniorityValue, string> = {
  intern: "Intern",
  junior: "Junior",
  mid: "Mid",
  senior: "Senior",
  lead: "Lead",
};

export type ChipEdit =
  | { field: "city"; cities: string[]; remote_ok: boolean }
  | { field: "min_salary"; min_lpa: number | null; exclude_estimates: boolean }
  | { field: "work_mode"; modes: WorkMode[] }
  | { field: "seniority"; seniority: SeniorityValue[] };

export type RelaxFilter =
  "geo" | "city" | "work_mode" | "seniority" | "min_salary" | "estimates";

/** The filters the API actually applied, as explicit URL state (auto off). */
export function stateFromFilters(q: string, f: SearchFilters): SearchUrlState {
  return {
    q,
    city: [...(f.cities ?? [])],
    remote_ok: f.remote_ok ?? false,
    min_lpa: f.min_salary_lpa ?? null,
    no_est: f.exclude_estimates ?? false,
    mode: [...(f.work_modes ?? [])],
    seniority: [...(f.seniority ?? [])],
    auto: false,
  };
}

export function applyChipEdit(
  base: SearchUrlState,
  edit: ChipEdit,
): SearchUrlState {
  const next = { ...base, auto: false };
  switch (edit.field) {
    case "city":
      next.city = [...edit.cities];
      next.remote_ok = edit.cities.length > 0 && edit.remote_ok;
      break;
    case "min_salary":
      next.min_lpa = edit.min_lpa;
      next.no_est = edit.min_lpa !== null && edit.exclude_estimates;
      break;
    case "work_mode":
      next.mode = [...edit.modes];
      break;
    case "seniority":
      next.seniority = [...edit.seniority];
      break;
  }
  return next;
}

export function removeChip(
  base: SearchUrlState,
  field: FilterField,
): SearchUrlState {
  switch (field) {
    case "city":
      return applyChipEdit(base, { field, cities: [], remote_ok: false });
    case "min_salary":
      return applyChipEdit(base, {
        field,
        min_lpa: null,
        exclude_estimates: false,
      });
    case "work_mode":
      return applyChipEdit(base, { field, modes: [] });
    case "seniority":
      return applyChipEdit(base, { field, seniority: [] });
  }
}

export function applyRelax(
  base: SearchUrlState,
  filter: RelaxFilter,
): SearchUrlState {
  switch (filter) {
    case "min_salary":
      return removeChip(base, "min_salary");
    case "city":
      return removeChip(base, "city");
    case "work_mode":
      return removeChip(base, "work_mode");
    case "seniority":
      return removeChip(base, "seniority");
    case "estimates":
      return { ...base, no_est: false, auto: false };
    case "geo":
      return { ...base, auto: false };
  }
}

function lpa(n: number | null | undefined): string {
  return `₹${n ?? 0} LPA`;
}

export type CountItem = { filter: RelaxFilter; count: number };

export function setAsideText(
  universe: number,
  setAside: readonly CountItem[],
  filters: SearchFilters,
): string | null {
  const parts: string[] = [];
  for (const { filter, count } of setAside) {
    if (count <= 0) continue;
    switch (filter) {
      case "min_salary":
        parts.push(`${count} below ${lpa(filters.min_salary_lpa)}`);
        break;
      case "city":
        parts.push(`${count} outside ${(filters.cities ?? []).join(", ")}`);
        break;
      case "work_mode":
        parts.push(
          `${count} not ${(filters.work_modes ?? [])
            .map((m) => WORK_MODE_LABEL[m].toLowerCase())
            .join(", ")}`,
        );
        break;
      case "seniority":
        parts.push(`${count} not ${(filters.seniority ?? []).join(", ")}`);
        break;
      case "estimates":
        parts.push(`${count} with estimated salary`);
        break;
      case "geo":
        parts.push(`${count} US/EU-only remote`);
        break;
    }
  }
  if (parts.length === 0) return null;
  return `Of the ${universe} most relevant jobs, set aside: ${parts.join(" · ")}`;
}

/** Label for one relaxation; null for filters the user cannot relax (geo). */
export function relaxLabel(
  option: CountItem,
  filters: SearchFilters,
): string | null {
  const gain = `(+${option.count} ${option.count === 1 ? "job" : "jobs"})`;
  switch (option.filter) {
    case "min_salary":
      return `Remove ${lpa(filters.min_salary_lpa)} minimum ${gain}`;
    case "city":
      return `Include any city ${gain}`;
    case "seniority":
      return `Any seniority ${gain}`;
    case "work_mode":
      return `Any work mode ${gain}`;
    case "estimates":
      return `Include estimated salaries ${gain}`;
    case "geo":
      return null;
  }
}

export type RelaxItem = { filter: RelaxFilter; label: string };

/** Relaxations with a gain, biggest first, at most 4. */
export function relaxItems(
  relax: readonly CountItem[],
  filters: SearchFilters,
): RelaxItem[] {
  return relax
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count)
    .flatMap((r) => {
      const label = relaxLabel(r, filters);
      return label === null ? [] : [{ filter: r.filter, label }];
    })
    .slice(0, 4);
}
