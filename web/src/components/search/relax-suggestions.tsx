"use client";

import type { RelaxOption, SearchFilters } from "@/lib/api";
import { applyRelax, relaxItems, stateFromFilters } from "@/lib/filters";
import type { SearchUrlState } from "@/lib/search-params";
import { Button } from "@/components/ui/button";
import { requestFocus } from "./filter-chips";

export function RelaxSuggestions({
  q,
  relax,
  filters,
  heading,
  onApply,
}: {
  q: string;
  relax: RelaxOption[];
  filters: SearchFilters;
  /** Show the "Too few matches? Try:" heading (omitted inside the empty state). */
  heading: boolean;
  onApply: (state: SearchUrlState) => void;
}) {
  const items = relaxItems(relax, filters);
  if (items.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {heading ? (
        <p className="text-sm leading-normal font-semibold">
          Too few matches? Try:
        </p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <Button
            key={item.filter}
            type="button"
            variant="outline"
            onClick={() => {
              requestFocus({ kind: "heading" });
              onApply(applyRelax(stateFromFilters(q, filters), item.filter));
            }}
            className="h-10 px-4 font-semibold"
          >
            {item.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
