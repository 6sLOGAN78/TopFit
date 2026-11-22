import type { Metadata } from "next";
import "./globals.css";
import { Inter } from "next/font/google";
import { cn } from "@/lib/utils";
import { Providers } from "./providers";
import { SiteHeader } from "@/components/search/site-header";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "TopFit · Jobs ranked for you",
  description: "Search fresh, open jobs and see why each one matched.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={cn("h-full antialiased", "font-sans", inter.variable)}
    >
      <body className="flex min-h-full flex-col">
        <Providers>
          <SiteHeader />
          <div className="flex-1">{children}</div>
          <footer className="mt-12 border-t py-6">
            <p className="mx-auto w-full max-w-3xl px-4 text-xs leading-[1.4] text-muted-foreground sm:px-6">
              Job data from public job-board APIs. Each listing links to its
              original source.
            </p>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
