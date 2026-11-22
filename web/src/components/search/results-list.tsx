"use client";

import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  metaApiMetaGetOptions,
  searchJobsApiJobsSearchGetInfiniteOptions,
} from "@/lib/api/@tanstack/react-query.gen";
import { useParseModel } from "@/lib/parse-model";
import { toSearchQuery, type SearchUrlState } from "@/lib/search-params";
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
import { FilterChips, takeFocus } from "./filter-chips";
import { ParseSummary } from "./parse-summary";
import { RelaxSuggestions } from "./relax-suggestions";
import { EXAMPLE_QUERIES } from "./search-hero";
import { SetAsideLine } from "./set-aside-line";

export function ResultsList({
  state,
  onSearch,
  onApply,
}: {
  state: SearchUrlState;
  onSearch: (q: string) => void;
  onApply: (state: SearchUrlState) => void;
}) {
  const meta = useQuery(metaApiMetaGetOptions());
  const parseModel = useParseModel(meta.data?.parse_models);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [loadedMore, setLoadedMore] = useState<string | null>(null);

  const query = useInfiniteQuery({
    ...searchJobsApiJobsSearchGetInfiniteOptions({
      query: toSearchQuery(state, parseModel),
    }),
    initialPageParam: { query: toSearchQuery(state, parseModel) },
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });

  useEffect(() => {
    if (query.isSuccess && takeFocus("heading")) headingRef.current?.focus();
  }, [query.isSuccess]);

  const pages = query.data?.pages ?? [];
  const items = pages.flatMap((p) => p.items);
  const first = pages[0];
  const degraded = first?.degraded ?? false;
  const parsed = first?.parsed;
  const relax = first?.relax ?? [];
  const canRelax = parsed !== undefined && relax.some((r) => r.count > 0);
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

      {parsed && first ? (
        <div className="flex flex-col gap-2">
          <ParseSummary parsed={parsed} />
          <FilterChips
            q={state.q}
            chips={parsed.chips ?? []}
            filters={parsed.filters}
            meta={meta.data}
            onApply={onApply}
          />
          <SetAsideLine
            universe={first.set_aside_universe}
            setAside={first.set_aside}
            filters={parsed.filters}
          />
        </div>
      ) : null}

      <div className="mt-2 flex items-baseline gap-2">
        <h2
          ref={headingRef}
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
              {canRelax
                ? "No jobs match all your filters"
                : "No open jobs match this search"}
            </EmptyTitle>
            <EmptyDescription>
              {canRelax
                ? "Try removing one of the filters below, or search with different words."
                : "Try broader words, like one role or one skill. Or start from an example:"}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            {canRelax && parsed ? (
              <RelaxSuggestions
                q={state.q}
                relax={relax}
                filters={parsed.filters}
                heading={false}
                onApply={onApply}
              />
            ) : (
              EXAMPLE_QUERIES.map((q) => (
                <Button
                  key={q}
                  type="button"
                  variant="outline"
                  onClick={() => onSearch(q)}
                  className="h-10 px-4 font-semibold"
                >
                  {q}
                </Button>
              ))
            )}
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
      ) : null}

      {items.length >= 1 && items.length <= 4 && parsed ? (
        <RelaxSuggestions
          q={state.q}
          relax={relax}
          filters={parsed.filters}
          heading
          onApply={onApply}
        />
      ) : null}

      {!nextCursor && items.length >= 5 ? (
        <p className="text-center text-sm leading-normal text-muted-foreground">
          That&apos;s every match for this search.
        </p>
      ) : null}
    </section>
  );
}
