import type { SearchFilters, SetAsideCount } from "@/lib/api";
import { setAsideText } from "@/lib/filters";

export function SetAsideLine({
  universe,
  setAside,
  filters,
}: {
  universe: number;
  setAside: SetAsideCount[];
  filters: SearchFilters;
}) {
  const text = setAsideText(universe, setAside, filters);
  if (text === null) return null;
  return <p className="text-sm leading-normal text-muted-foreground">{text}</p>;
}
