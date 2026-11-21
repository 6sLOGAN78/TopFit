"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type Check = { ok: boolean; version?: string | null; dim?: number };

type Readyz = {
  status: "ok" | "unavailable";
  checks: {
    db: Check;
    pgvector: Check & { min?: string };
    model: Check;
    embedding: Check;
  };
};

type Row = { label: string; ok: boolean; detail?: string };

type State =
  | { phase: "running" }
  | { phase: "done"; rows: Row[] }
  | { phase: "error"; message: string };

async function ping(): Promise<boolean> {
  const res = await fetch("/api/session/ping", {
    cache: "no-store",
    credentials: "same-origin",
  });
  const body = (await res.json()) as { cookie_received: boolean };
  return body.cookie_received === true;
}

async function runChecks(): Promise<Row[]> {
  const health = await fetch("/api/healthz", { cache: "no-store" });
  const ready = await fetch("/api/readyz", { cache: "no-store" });
  const readyBody = (await ready.json()) as Readyz;
  const first = await ping();
  const second = await ping();
  const c = readyBody.checks;
  return [
    { label: "API reachable", ok: health.status === 200 },
    { label: "Database reachable", ok: c.db.ok },
    {
      label: "pgvector >= 0.8.3",
      ok: c.pgvector.ok,
      detail: c.pgvector.version ? `v${c.pgvector.version}` : undefined,
    },
    {
      label: "Embedding model loaded",
      ok: c.model.ok && c.embedding.ok,
      detail: c.embedding.dim ? `${c.embedding.dim}-d` : undefined,
    },
    { label: "Cookie round-trip", ok: !first && second },
  ];
}

export function StatusPanel() {
  const [state, setState] = useState<State>({ phase: "running" });

  const run = useCallback(async () => {
    setState({ phase: "running" });
    try {
      setState({ phase: "done", rows: await runChecks() });
    } catch {
      setState({ phase: "error", message: "Could not reach the API." });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void run();
  }, [run]);

  const running = state.phase === "running";

  return (
    <section aria-label="System status" className="space-y-4">
      <ul className="space-y-2">
        {state.phase === "done" &&
          state.rows.map((row) => (
            <li key={row.label} className="flex items-center gap-2">
              <span aria-hidden="true">{row.ok ? "✓" : "✗"}</span>
              <span>{row.label}</span>
              <span className="text-sm opacity-70">
                {row.ok ? "pass" : "fail"}
                {row.detail ? ` (${row.detail})` : ""}
              </span>
            </li>
          ))}
        {running && <li>checking…</li>}
        {state.phase === "error" && <li>✗ {state.message}</li>}
      </ul>
      <Button onClick={() => void run()} disabled={running}>
        Re-run checks
      </Button>
    </section>
  );
}
