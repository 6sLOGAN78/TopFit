"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Settings2 } from "lucide-react";
import { metaApiMetaGetOptions } from "@/lib/api/@tanstack/react-query.gen";
import { setParseModel, useParseModel } from "@/lib/parse-model";
import type { ParseModel } from "@/lib/search-params";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

const MODELS: { value: ParseModel; name: string; description: string }[] = [
  {
    value: "haiku",
    name: "Claude Haiku",
    description:
      "Reads the parts of your search that rules can't place, such as unusual city names.",
  },
  {
    value: "jev",
    name: "Jev",
    description: "TypeSafe AI's decision model. Experimental.",
  },
];

export function SettingsSheet() {
  const meta = useQuery(metaApiMetaGetOptions());
  const allowed: ParseModel[] = meta.data?.parse_models ?? ["haiku"];
  const parseModel = useParseModel(allowed);
  const [note, setNote] = useState("");
  const choices = MODELS.filter((m) => allowed.includes(m.value));

  return (
    <Sheet>
      <SheetTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            aria-label="Search settings"
            className="size-11"
          />
        }
      >
        <Settings2 aria-hidden="true" className="size-5" />
      </SheetTrigger>
      <SheetContent className="w-full border-l border-hairline bg-background sm:max-w-sm">
        <SheetHeader>
          <SheetTitle className="text-base font-semibold">
            Search settings
          </SheetTitle>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4">
          <Label id="parse-model-label" className="text-sm font-semibold">
            Query parsing model
          </Label>
          {choices.length > 1 ? (
            <RadioGroup
              aria-labelledby="parse-model-label"
              value={parseModel}
              onValueChange={(v) => {
                const next = v as ParseModel;
                setParseModel(next);
                setNote(
                  `Search re-run with ${MODELS.find((m) => m.value === next)?.name}`,
                );
              }}
            >
              {choices.map((m) => (
                <div key={m.value} className="flex items-start gap-3 py-2">
                  <RadioGroupItem
                    id={`parse-model-${m.value}`}
                    value={m.value}
                    className="mt-0.5 data-checked:border-brand data-checked:bg-brand"
                  />
                  <Label
                    htmlFor={`parse-model-${m.value}`}
                    className="flex flex-col items-start gap-1"
                  >
                    <span className="text-sm font-semibold">{m.name}</span>
                    <span className="text-sm leading-normal font-normal text-muted-foreground">
                      {m.description}
                    </span>
                  </Label>
                </div>
              ))}
            </RadioGroup>
          ) : (
            <p className="text-sm">Claude Haiku</p>
          )}
          <p className="text-xs leading-[1.4] text-muted-foreground">
            Rules always read your search first. The model only sees the words
            the rules couldn&apos;t place, and never your other details. Saved
            in this browser.
          </p>
          <p className="sr-only" aria-live="polite" role="status">
            {note}
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}
