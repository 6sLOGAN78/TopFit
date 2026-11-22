import type { ParsedSummary } from "@/lib/api";

const SOURCE_TEXT: Record<
  NonNullable<ParsedSummary["parse_source"]>,
  string
> = {
  rules: "Read by rules",
  "rules+haiku": "Read by rules + Claude Haiku",
  "rules+jev": "Read by rules + Jev",
};

export function ParseSummary({ parsed }: { parsed: ParsedSummary }) {
  const hasChips = (parsed.chips ?? []).length > 0;
  return (
    <p className="text-xs leading-[1.4] text-muted-foreground">
      {hasChips
        ? SOURCE_TEXT[parsed.parse_source ?? "rules"]
        : "No filters found in your search. Add one below."}
    </p>
  );
}
