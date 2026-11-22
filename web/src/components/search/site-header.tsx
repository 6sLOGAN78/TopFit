import Link from "next/link";
import { SettingsSheet } from "./settings-sheet";

export function SiteHeader() {
  return (
    <header className="h-14 border-b bg-background">
      <div className="mx-auto flex h-full w-full max-w-3xl items-center justify-between px-4 sm:px-6">
        <Link
          href="/"
          className="rounded-md text-base leading-normal font-semibold text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          TopFit
        </Link>
        <SettingsSheet />
      </div>
    </header>
  );
}
