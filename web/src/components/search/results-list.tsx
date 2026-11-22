import type { SearchUrlState } from "@/lib/search-params";

// Placeholder replaced by the full implementation in the next commit.
export function ResultsList({
  state,
}: {
  state: SearchUrlState;
  onSearch: (q: string) => void;
}) {
  return <p className="sr-only">{state.q}</p>;
}
