"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BLANK_QUERY_MESSAGE } from "./search-hero";

export function SearchBar({
  q,
  onSearch,
}: {
  q: string;
  onSearch: (q: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const [value, setValue] = useState(q);
  const [error, setError] = useState(false);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (value.trim() === "") {
      setError(true);
      inputRef.current?.focus();
      return;
    }
    setError(false);
    onSearch(value.trim());
  }

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      role="search"
      className="flex flex-col gap-2"
    >
      <div className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="bar-q" className="sr-only">
          What job are you looking for?
        </label>
        <Input
          id="bar-q"
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. backend python bengaluru 10 LPA remote ok"
          autoComplete="off"
          maxLength={500}
          aria-invalid={error}
          aria-describedby={error ? errorId : undefined}
          className="h-11 flex-1 bg-background text-base focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand"
        />
        <Button
          type="submit"
          className="h-11 bg-brand px-4 text-brand-foreground hover:bg-brand-pressed active:bg-brand-pressed max-sm:w-full"
        >
          Search jobs
        </Button>
      </div>
      {error ? (
        <p
          id={errorId}
          className="flex items-center gap-1 text-sm leading-normal text-destructive"
        >
          <CircleAlert className="size-4" aria-hidden="true" />
          {BLANK_QUERY_MESSAGE}
        </p>
      ) : null}
    </form>
  );
}
