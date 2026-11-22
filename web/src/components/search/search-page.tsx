"use client";

import { useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  readSearchParams,
  writeSearchParams,
  type SearchUrlState,
} from "@/lib/search-params";
import { SearchBar } from "./search-bar";
import { SearchHero } from "./search-hero";
import { ResultsList } from "./results-list";

const EMPTY_STATE: SearchUrlState = {
  q: "",
  city: [],
  remote_ok: false,
  min_lpa: null,
  no_est: false,
  mode: [],
  seniority: [],
  auto: true,
};

export function SearchPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const state = useMemo(() => readSearchParams(new URLSearchParams(sp)), [sp]);

  // A new search resets auto to true and drops explicit filters (D-01).
  function search(q: string) {
    router.push(`/?${writeSearchParams({ ...EMPTY_STATE, q })}`);
  }

  // Chip edits and relaxations write explicit filters (auto off) to the URL.
  function apply(next: SearchUrlState) {
    router.push(`/?${writeSearchParams(next)}`);
  }

  if (state.q.trim() === "") {
    return <SearchHero onSearch={search} />;
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 pt-6 sm:px-6">
      <SearchBar key={state.q} q={state.q} onSearch={search} />
      <ResultsList
        key={sp.toString()}
        state={state}
        onSearch={search}
        onApply={apply}
      />
    </div>
  );
}
