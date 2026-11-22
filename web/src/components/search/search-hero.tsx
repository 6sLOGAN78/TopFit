"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { metaApiMetaGetOptions } from "@/lib/api/@tanstack/react-query.gen";
import { formatCount } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

export const EXAMPLE_QUERIES = [
  "backend python bengaluru 10 LPA remote ok",
  "react frontend developer pune hybrid",
  "data analyst fresher sql",
  "devops kubernetes remote india",
] as const;

export const BLANK_QUERY_MESSAGE = "Type a role, skill or city to search.";

export function SearchHero({ onSearch }: { onSearch: (q: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const [value, setValue] = useState("");
  const [error, setError] = useState(false);
  const meta = useQuery(metaApiMetaGetOptions());

  useEffect(() => {
    if (window.matchMedia("(pointer: fine)").matches) inputRef.current?.focus();
  }, []);

  function submit(q: string) {
    if (q.trim() === "") {
      setError(true);
      inputRef.current?.focus();
      return;
    }
    setError(false);
    onSearch(q.trim());
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit(value);
  }

  return (
    <section className="bg-navy text-on-dark">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-12 sm:px-6 sm:py-16 sm:text-center">
        <div className="flex flex-col gap-2">
          <h1 className="text-[36px] leading-[1.2] font-semibold tracking-[-0.5px] text-on-dark">
            Find open jobs that fit you
          </h1>
          <p className="text-base leading-[1.55] text-on-dark-muted">
            Describe the job you want in plain words. We search fresh listings
            from public job boards, apply the filters you mention, and show why
            each job matched.
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          noValidate
          role="search"
          className="mx-auto flex w-full max-w-2xl flex-col gap-2 sm:text-left"
        >
          <div className="flex flex-col gap-2 sm:flex-row">
            <label htmlFor="hero-q" className="sr-only">
              What job are you looking for?
            </label>
            <Input
              id="hero-q"
              ref={inputRef}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. backend python bengaluru 10 LPA remote ok"
              autoComplete="off"
              maxLength={500}
              aria-invalid={error}
              aria-describedby={error ? errorId : undefined}
              className="h-11 flex-1 border-transparent bg-on-dark text-base text-foreground focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand"
            />
            <Button
              type="submit"
              className="h-11 bg-on-dark px-4 text-foreground hover:bg-surface focus-visible:ring-on-dark active:bg-hairline max-sm:w-full"
            >
              Search jobs
            </Button>
          </div>
          {error ? (
            <p
              id={errorId}
              className="flex items-center gap-1 text-sm leading-normal text-on-dark"
            >
              <CircleAlert className="size-4" aria-hidden="true" />
              {BLANK_QUERY_MESSAGE}
            </p>
          ) : null}
        </form>

        <div className="flex flex-col gap-2 sm:items-center">
          <div className="flex flex-wrap items-center gap-2 sm:justify-center">
            <span className="text-sm leading-normal font-semibold text-on-dark">
              Try:
            </span>
            {EXAMPLE_QUERIES.map((q) => (
              <Button
                key={q}
                type="button"
                variant="outline"
                onClick={() => {
                  setValue(q);
                  submit(q);
                }}
                className="h-10 border-on-dark-muted bg-transparent px-4 text-on-dark hover:bg-white/10 hover:text-on-dark focus-visible:ring-on-dark"
              >
                {q}
              </Button>
            ))}
          </div>
        </div>

        {meta.isPending ? (
          <Skeleton className="h-5 w-56 bg-white/15 sm:mx-auto" />
        ) : meta.data ? (
          <p className="text-sm leading-normal text-on-dark-muted">
            <span className="font-semibold text-on-dark tabular-nums">
              {formatCount(meta.data.active_jobs)}
            </span>{" "}
            open jobs in the index right now
          </p>
        ) : null}
      </div>
    </section>
  );
}
