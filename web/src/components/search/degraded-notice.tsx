import { Info } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function DegradedNotice() {
  return (
    <Alert className="border-border">
      <Info className="text-muted-foreground" aria-hidden="true" />
      <AlertTitle className="text-foreground">
        Using simple search rules
      </AlertTitle>
      <AlertDescription className="text-muted-foreground">
        Smart query reading is unavailable right now, so we read your search
        with rules only. Check the filters above and fix any we got wrong.
      </AlertDescription>
    </Alert>
  );
}
