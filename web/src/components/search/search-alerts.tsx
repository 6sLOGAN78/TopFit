import { CircleAlert, Clock } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

type ApiErrorBody = { error?: { code?: string; details?: unknown } };

/** Minutes to wait if this is a rate-limit error, otherwise null. */
export function rateLimitMinutes(error: unknown): number | null {
  const body = error as ApiErrorBody | null;
  if (
    !body ||
    typeof body !== "object" ||
    body.error?.code !== "rate_limited"
  ) {
    return null;
  }
  const details = body.error.details as { retry_after?: unknown } | undefined;
  const seconds = Number(details?.retry_after);
  return Number.isFinite(seconds) && seconds > 0
    ? Math.max(1, Math.ceil(seconds / 60))
    : 1;
}

export function SearchAlerts({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const minutes = rateLimitMinutes(error);
  if (minutes !== null) {
    return (
      <Alert className="border-transparent bg-warning-surface text-warning">
        <Clock aria-hidden="true" />
        <AlertTitle className="text-warning">Search limit reached</AlertTitle>
        <AlertDescription className="text-warning">
          Guests can run 60 searches an hour. Try again in {minutes}{" "}
          {minutes === 1 ? "minute" : "minutes"}.
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertTitle>Search didn&apos;t load</AlertTitle>
      <AlertDescription className="text-foreground">
        <p>
          Something went wrong on our side or with your connection. Try again in
          a moment.
        </p>
        <Button
          type="button"
          variant="outline"
          onClick={onRetry}
          className="mt-2 h-10 px-4 font-semibold text-foreground"
        >
          Retry search
        </Button>
      </AlertDescription>
    </Alert>
  );
}
