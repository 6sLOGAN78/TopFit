"use client";

import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { searchJobsApiJobsSearchGetInfiniteOptions } from "@/lib/api/@tanstack/react-query.gen";
import {
  toSearchQuery,
  type ParseModel,
  type SearchUrlState,
} from "@/lib/search-params";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { DegradedNotice } from "./degraded-notice";
import { JobCard } from "./job-card";
import { SearchAlerts } from "./search-alerts";
import { EXAMPLE_QUERIES } from "./search-hero";

const PARSE_MODEL_KEY = "topfit.parseModel";

// 03-08 replaces this with lib/parse-model.ts (validated against /api/meta).
function readParseModel(): ParseModel {
  if (typeof window === "undefined") return "haiku";
  try {
    return window.localStorage.getItem(PARSE_MODEL_KEY) === "jev"
      ? "jev"
      : "haiku";
  } catch {
    return "haiku";
  }
}

export function ResultsList({
  state,
  onSearch,
}: {
  state: SearchUrlState;
  onSearch: (q: string) => void;
}) {
  const [parseModel] = useState<ParseModel>(readParseModel);
  const [loadedMore, setLoadedMore] = useState<string | null>(null);

  const query = useInfiniteQuery({
    ...searchJobsApiJobsSearchGetInfiniteOptions({
      query: toSearchQuery(state, parseModel),
    }),
    initialPageParam: { query: toSearchQuery(state, parseModel) },
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });

  const pages = query.data?.pages ?? [];
  const items = pages.flatMap((p) => p.items);
  const degraded = pages[0]?.degraded ?? false;
  const nextCursor =
    pages.length > 0 ? pages[pages.length - 1].next_cursor : null;
  const loading = query.isPending;
  const searching = query.isFetching && !query.isFetchingNextPage;

  async function loadMore() {
    if (query.isFetchingNextPage) return;
    const res = await query.fetchNextPage();
    if (!res.isError) setLoadedMore("20 more jobs loaded");
  }

  const announcement = searching
    ? "Searching…"
    : loadedMore
      ? loadedMore
      : query.isError
        ? ""
        : `${items.length} ${items.length === 1 ? "job" : "jobs"} found`;

  return (
    <section className="flex flex-col gap-4" aria-label="Search results">
      {query.isError ? (
        <SearchAlerts
          error={query.error}
          onRetry={() => {
            setLoadedMore(null);
            void query.refetch();
          }}
        />
      ) : null}
      {degraded ? <DegradedNotice /> : null}

      <div className="mt-2 flex items-baseline gap-2">
        <h2
          tabIndex={-1}
          className="text-base leading-normal font-semibold outline-none"
        >
          Top matches
        </h2>
        <p className="sr-only" aria-live="polite" role="status">
          {announcement}
        </p>
      </div>

      {loading ? (
        <div className="flex flex-col gap-4" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-44 rounded-xl" />
          ))}
        </div>
      ) : items.length === 0 && !query.isError ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle className="text-base">
              No open jobs match this search
            </EmptyTitle>
            <EmptyDescription>
              Try broader words, like one role or one skill. Or start from an
              example:
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {EXAMPLE_QUERIES.map((q) => (
              <Button
                key={q}
                type="button"
                variant="outline"
                onClick={() => onSearch(q)}
                className="h-10 px-4 font-semibold"
              >
                {q}
              </Button>
            ))}
          </EmptyContent>
        </Empty>
      ) : (
        <div className="flex flex-col gap-4" aria-busy={searching}>
          {items.map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </div>
      )}

      {nextCursor ? (
        <Button
          type="button"
          variant="outline"
          onClick={() => void loadMore()}
          aria-disabled={query.isFetchingNextPage}
          className="h-11 w-full px-4 font-semibold aria-disabled:opacity-50 sm:mx-auto sm:w-auto"
        >
          {query.isFetchingNextPage ? (
            <>
              <Spinner aria-hidden="true" />
              Loading more jobs…
            </>
          ) : (
            "Load more jobs"
          )}
        </Button>
      ) : items.length >= 5 ? (
        <p className="text-center text-sm leading-normal text-muted-foreground">
          That&apos;s every match for this search.
        </p>
      ) : null}
    </section>
  );
}
