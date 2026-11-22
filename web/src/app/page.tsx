import { Suspense } from "react";
import { SearchPage } from "@/components/search/search-page";

function PageFallback() {
  return <div className="h-72 bg-navy" aria-hidden="true" />;
}

export default function Home() {
  return (
    <main>
      <Suspense fallback={<PageFallback />}>
        <SearchPage />
      </Suspense>
    </main>
  );
}
