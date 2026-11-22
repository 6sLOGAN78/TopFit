import { useSyncExternalStore } from "react";
import type { ParseModel } from "./search-params";

export const PARSE_MODEL_KEY = "topfit.parseModel";

const ALL_MODELS: readonly ParseModel[] = ["haiku", "jev"];
const listeners = new Set<() => void>();

/** Stored parse model, or "haiku" when unset, SSR, unavailable or not in `allowed`. */
export function getParseModel(
  allowed: readonly ParseModel[] = ALL_MODELS,
): ParseModel {
  if (typeof window === "undefined") return "haiku";
  try {
    const stored = window.localStorage.getItem(PARSE_MODEL_KEY);
    return allowed.find((m) => m === stored) ?? "haiku";
  } catch {
    return "haiku";
  }
}

export function setParseModel(model: ParseModel): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PARSE_MODEL_KEY, model);
  } catch {
    // Storage blocked: the choice just won't persist.
  }
  for (const l of listeners) l();
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  const onStorage = (e: StorageEvent) => {
    if (e.key === PARSE_MODEL_KEY) onChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Reactive parse model shared by the header sheet and the results. */
export function useParseModel(
  allowed: readonly ParseModel[] = ALL_MODELS,
): ParseModel {
  return useSyncExternalStore(
    subscribe,
    () => getParseModel(allowed),
    () => "haiku",
  );
}
