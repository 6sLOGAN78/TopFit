"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Sparkles, X } from "lucide-react";
import type { Chip, MetaResponse, SearchFilters } from "@/lib/api";
import {
  applyChipEdit,
  FIELD_LABEL,
  FILTER_FIELDS,
  removeChip,
  SALARY_BUCKETS,
  SENIORITY_LABEL,
  stateFromFilters,
  WORK_MODE_LABEL,
  type FilterField,
} from "@/lib/filters";
import type {
  SearchUrlState,
  SeniorityValue,
  WorkMode,
} from "@/lib/search-params";
import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
  useComboboxAnchor,
} from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

// A chip edit re-keys the results, so focus is handed over through this
// module-level request and consumed once the new results mount.
type FocusRequest =
  { kind: "chip"; index: number } | { kind: "add" } | { kind: "heading" };
let pendingFocus: FocusRequest | null = null;

export function requestFocus(req: FocusRequest): void {
  pendingFocus = req;
}

export function takeFocus(kind: FocusRequest["kind"]): FocusRequest | null {
  if (pendingFocus?.kind !== kind) return null;
  const req = pendingFocus;
  pendingFocus = null;
  return req;
}

const pressed =
  "data-pressed:bg-foreground data-pressed:text-on-dark data-pressed:hover:bg-foreground";

function modelName(source: Chip["source"]): string | null {
  if (source === "haiku") return "Claude Haiku";
  if (source === "jev") return "Jev";
  return null;
}

function FieldEditor({
  field,
  filters,
  cities,
  onApply,
}: {
  field: FilterField;
  filters: SearchFilters;
  cities: string[];
  onApply: (edit: Parameters<typeof applyChipEdit>[1]) => void;
}) {
  const anchor = useComboboxAnchor();
  const [selCities, setSelCities] = useState<string[]>(filters.cities ?? []);
  const [remoteOk, setRemoteOk] = useState(filters.remote_ok ?? false);
  const [minLpa, setMinLpa] = useState<number | null>(
    filters.min_salary_lpa ?? null,
  );
  const [noEst, setNoEst] = useState(filters.exclude_estimates ?? false);
  const [modes, setModes] = useState<WorkMode[]>(filters.work_modes ?? []);
  const [seniority, setSeniority] = useState<SeniorityValue[]>(
    filters.seniority ?? [],
  );

  const cityItems = useMemo(
    () => Array.from(new Set([...cities, ...selCities])).sort(),
    [cities, selCities],
  );
  const salaryItems = useMemo(() => {
    const buckets: number[] = [...SALARY_BUCKETS];
    if (minLpa !== null && !buckets.includes(minLpa)) {
      buckets.push(minLpa);
      buckets.sort((a, b) => a - b);
    }
    return [
      { value: "any", label: "Any" },
      ...buckets.map((n) => ({ value: String(n), label: `₹${n} LPA+` })),
    ];
  }, [minLpa]);

  function apply() {
    switch (field) {
      case "city":
        return onApply({ field, cities: selCities, remote_ok: remoteOk });
      case "min_salary":
        return onApply({ field, min_lpa: minLpa, exclude_estimates: noEst });
      case "work_mode":
        return onApply({ field, modes });
      case "seniority":
        return onApply({ field, seniority });
    }
  }

  return (
    <div className="flex flex-col gap-4 p-1">
      {field === "city" ? (
        <>
          <div className="flex flex-col gap-2">
            <Label>City</Label>
            <Combobox
              multiple
              items={cityItems}
              value={selCities}
              onValueChange={(v) => setSelCities(v)}
            >
              <ComboboxChips ref={anchor}>
                {selCities.map((c) => (
                  <ComboboxChip key={c}>{c}</ComboboxChip>
                ))}
                <ComboboxChipsInput
                  aria-label="City"
                  placeholder={selCities.length === 0 ? "Search cities" : ""}
                />
              </ComboboxChips>
              <ComboboxContent anchor={anchor}>
                <ComboboxEmpty>No city found.</ComboboxEmpty>
                <ComboboxList>
                  {(item: string) => (
                    <ComboboxItem key={item} value={item}>
                      {item}
                    </ComboboxItem>
                  )}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="chip-remote-ok" className="leading-snug">
              Also include remote jobs open to India
            </Label>
            <Switch
              id="chip-remote-ok"
              checked={remoteOk}
              onCheckedChange={setRemoteOk}
              className="data-checked:bg-foreground"
            />
          </div>
        </>
      ) : null}

      {field === "min_salary" ? (
        <>
          <div className="flex flex-col gap-2">
            <Label>Min salary</Label>
            <Select
              items={salaryItems}
              value={minLpa === null ? "any" : String(minLpa)}
              onValueChange={(v) =>
                setMinLpa(v === null || v === "any" ? null : Number(v))
              }
            >
              <SelectTrigger aria-label="Min salary" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {salaryItems.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="chip-no-est" className="leading-snug">
              Exclude estimated salaries
            </Label>
            <Switch
              id="chip-no-est"
              checked={noEst}
              onCheckedChange={setNoEst}
              className="data-checked:bg-foreground"
            />
          </div>
        </>
      ) : null}

      {field === "work_mode" ? (
        <div className="flex flex-col gap-2">
          <Label id="chip-mode-label">Work mode</Label>
          <ToggleGroup
            multiple
            variant="outline"
            aria-labelledby="chip-mode-label"
            value={modes}
            onValueChange={(v) => setModes(v as WorkMode[])}
            className="flex-wrap"
          >
            {(Object.keys(WORK_MODE_LABEL) as WorkMode[]).map((m) => (
              <ToggleGroupItem key={m} value={m} className={pressed}>
                {WORK_MODE_LABEL[m]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      ) : null}

      {field === "seniority" ? (
        <div className="flex flex-col gap-2">
          <Label id="chip-seniority-label">Seniority</Label>
          <ToggleGroup
            multiple
            variant="outline"
            aria-labelledby="chip-seniority-label"
            value={seniority}
            onValueChange={(v) => setSeniority(v as SeniorityValue[])}
            className="flex-wrap"
          >
            {(Object.keys(SENIORITY_LABEL) as SeniorityValue[]).map((s) => (
              <ToggleGroupItem key={s} value={s} className={pressed}>
                {SENIORITY_LABEL[s]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      ) : null}

      <Button
        type="button"
        onClick={apply}
        className="h-10 bg-foreground px-4 font-semibold text-on-dark hover:bg-foreground/90"
      >
        Apply filter
      </Button>
    </div>
  );
}

export function FilterChips({
  q,
  chips,
  filters,
  meta,
  onApply,
}: {
  q: string;
  chips: Chip[];
  filters: SearchFilters;
  meta: MetaResponse | undefined;
  onApply: (state: SearchUrlState) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [openField, setOpenField] = useState<FilterField | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [picked, setPicked] = useState<FilterField | null>(null);
  const base = useMemo(() => stateFromFilters(q, filters), [q, filters]);
  const cities = meta?.cities ?? [];

  const set = new Set(chips.map((c) => c.field));
  const missing = FILTER_FIELDS.filter((f) => !set.has(f));

  useEffect(() => {
    const req = takeFocus("chip") ?? takeFocus("add");
    if (!req || !rootRef.current) return;
    const labels =
      rootRef.current.querySelectorAll<HTMLElement>("[data-chip-label]");
    const target =
      req.kind === "chip" && labels[req.index]
        ? labels[req.index]
        : rootRef.current.querySelector<HTMLElement>("[data-add-filter]");
    (target ?? labels[0])?.focus();
  }, []);

  function apply(edit: Parameters<typeof applyChipEdit>[1]) {
    setOpenField(null);
    setAddOpen(false);
    setPicked(null);
    requestFocus({ kind: "add" });
    onApply(applyChipEdit(base, edit));
  }

  function remove(chip: Chip, index: number) {
    requestFocus({ kind: "chip", index });
    onApply(removeChip(base, chip.field));
  }

  return (
    <div ref={rootRef} className="flex flex-wrap items-center gap-2">
      {chips.map((chip, i) => {
        const model = modelName(chip.source);
        return (
          <span
            key={chip.field}
            className="inline-flex h-8 items-center rounded-full bg-foreground pr-1 pl-3 text-sm text-on-dark"
          >
            <Popover
              open={openField === chip.field}
              onOpenChange={(o) => setOpenField(o ? chip.field : null)}
            >
              <PopoverTrigger
                data-chip-label
                className="inline-flex items-center gap-1 rounded-full pr-1 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {model ? (
                  <>
                    <Sparkles aria-hidden="true" className="size-3.5" />
                    <span className="sr-only">suggested by {model}</span>
                  </>
                ) : null}
                <span className="font-normal">{chip.label}</span>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-72 gap-4 p-3">
                <FieldEditor
                  field={chip.field}
                  filters={filters}
                  cities={cities}
                  onApply={apply}
                />
              </PopoverContent>
            </Popover>
            <button
              type="button"
              aria-label={`Remove filter: ${FIELD_LABEL[chip.field]} ${chip.value}`}
              onClick={() => remove(chip, i)}
              className="inline-flex size-6 items-center justify-center rounded-full outline-none hover:bg-on-dark/15 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <X aria-hidden="true" className="size-4" />
            </button>
          </span>
        );
      })}

      {missing.length > 0 ? (
        <Popover
          open={addOpen}
          onOpenChange={(o) => {
            setAddOpen(o);
            if (!o) setPicked(null);
          }}
        >
          <PopoverTrigger
            data-add-filter
            className="inline-flex h-8 items-center gap-1 rounded-full border border-hairline-strong bg-transparent px-3 text-sm text-muted-foreground outline-none hover:bg-surface focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            <Plus aria-hidden="true" className="size-4" />
            Add filter
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 gap-1 p-3">
            {picked === null ? (
              missing.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setPicked(f)}
                  className="h-9 rounded-md px-2 text-left text-sm outline-none hover:bg-surface focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {FIELD_LABEL[f]}
                </button>
              ))
            ) : (
              <FieldEditor
                field={picked}
                filters={filters}
                cities={cities}
                onApply={apply}
              />
            )}
          </PopoverContent>
        </Popover>
      ) : null}
    </div>
  );
}
