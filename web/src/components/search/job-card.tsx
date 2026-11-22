"use client";

import { useId, type ReactNode } from "react";
import {
  Building2,
  Check,
  CircleHelp,
  ExternalLink,
  Globe,
  TriangleAlert,
} from "lucide-react";
import type { JobResult } from "@/lib/api";
import {
  formatChecked,
  formatPosted,
  formatSalary,
  placeText,
  regionTag,
  staleReasons,
  type RegionTag,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

type TagStyle = "neutral" | "outline" | "green" | "purple" | "orange";

const TAG_BASE =
  "inline-flex h-5 items-center gap-1 rounded-sm px-2 text-xs leading-[1.4] font-semibold whitespace-nowrap [&_svg]:size-3 [&_svg]:shrink-0";

const TAG_STYLES: Record<TagStyle, string> = {
  neutral: "bg-surface text-foreground",
  outline: "border border-hairline-strong bg-transparent text-muted-foreground",
  green: "bg-success-surface text-success",
  purple: "bg-brand-tint text-brand-tint-foreground",
  orange: "bg-warning-surface text-warning",
};

function tagClass(style: TagStyle, extra?: string) {
  return cn(TAG_BASE, TAG_STYLES[style], extra);
}

const REGION_ICONS = {
  Globe,
  CircleHelp,
  Building2,
} as const;

function RegionBadge({ tag }: { tag: RegionTag }) {
  const Icon = tag.icon ? REGION_ICONS[tag.icon] : null;
  return (
    <span className={tagClass(tag.style)}>
      {Icon ? <Icon aria-hidden="true" /> : null}
      {tag.text}
    </span>
  );
}

function lowerFirst(s: string) {
  return s.charAt(0).toLowerCase() + s.slice(1);
}

function StaleTag({ reasons }: { reasons: JobResult["ghost_reasons"] }) {
  const sentences = staleReasons(reasons);
  const label = `May be stale: ${sentences
    .map((s) => lowerFirst(s).replace(/\.$/, ""))
    .join("; ")}`;
  return (
    <Popover>
      <PopoverTrigger
        aria-label={label}
        className={tagClass(
          "orange",
          "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        )}
      >
        <TriangleAlert aria-hidden="true" />
        May be stale
      </PopoverTrigger>
      <PopoverContent align="start" className="gap-2">
        <ul className="list-disc pl-4 text-sm leading-normal">
          {sentences.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
        <p className="text-xs leading-[1.4] text-muted-foreground">
          We show it anyway, ranked lower. Check the original posting before
          applying.
        </p>
      </PopoverContent>
    </Popover>
  );
}

function SkillRow({ job }: { job: JobResult }) {
  const matched = new Set(job.matched_skills.map((s) => s.toLowerCase()));
  const hits = job.skills.filter((s) => matched.has(s.toLowerCase()));
  const others = job.skills.filter((s) => !matched.has(s.toLowerCase()));
  const shown = [...hits, ...others].slice(0, 6);
  if (shown.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Skills">
      {shown.map((skill) => {
        const isHit = matched.has(skill.toLowerCase());
        const hitIndex = hits.indexOf(skill);
        const style: TagStyle = isHit && hitIndex < 3 ? "purple" : "neutral";
        return (
          <li key={skill} className={tagClass(style)}>
            {isHit ? (
              <>
                <Check aria-hidden="true" />
                <span className="sr-only">matches your search: </span>
              </>
            ) : null}
            {skill}
          </li>
        );
      })}
    </ul>
  );
}

function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

function Absolute({ iso, children }: { iso: string; children: ReactNode }) {
  return (
    <time dateTime={iso} title={new Date(iso).toLocaleString("en-IN")}>
      {children}
    </time>
  );
}

export function JobCard({
  job,
  now = new Date(),
}: {
  job: JobResult;
  now?: Date;
}) {
  const titleId = useId();
  const region = regionTag(job);
  const salary = formatSalary(job);
  const postedIso = job.posted_at ?? job.first_seen_at;

  const salaryTag = salary.estimate ? (
    <Tooltip>
      <TooltipTrigger
        render={<span tabIndex={0} />}
        className={tagClass(
          salary.style,
          "tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        {salary.text}
      </TooltipTrigger>
      <TooltipContent>
        Estimated by Adzuna. The employer did not state a salary.
      </TooltipContent>
    </Tooltip>
  ) : (
    <span className={tagClass(salary.style, "tabular-nums")}>
      {salary.text}
    </span>
  );

  return (
    <article
      aria-labelledby={titleId}
      className="flex flex-col gap-2 rounded-xl bg-card p-4 ring-1 ring-border sm:p-6"
    >
      <h3
        id={titleId}
        className="line-clamp-2 text-base leading-normal font-semibold text-foreground"
      >
        {job.title}
      </h3>
      <p
        className="truncate text-sm leading-normal text-charcoal"
        title={job.company ?? undefined}
      >
        {job.company ?? "Company not stated"} · {placeText(job)}
      </p>
      <div className="flex flex-wrap gap-2">
        {region ? <RegionBadge tag={region} /> : null}
        {salaryTag}
        {job.ghost_reasons.length > 0 ? (
          <StaleTag reasons={job.ghost_reasons} />
        ) : null}
      </div>
      <SkillRow job={job} />
      <p className="text-sm leading-normal text-muted-foreground">
        <span className="sr-only">Why it matched: </span>
        {job.why_matched}
      </p>
      <div className="flex items-center justify-between gap-2 border-t pt-4">
        <p className="text-xs leading-[1.4] text-muted-foreground">
          via {job.source_display_name} ·{" "}
          <Absolute iso={postedIso}>{formatPosted(job, now)}</Absolute> ·{" "}
          <Absolute iso={job.last_seen_at}>
            {formatChecked(job.last_seen_at, now)}
          </Absolute>
        </p>
        {isHttpUrl(job.url) ? (
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              buttonVariants({ variant: "outline" }),
              "h-10 shrink-0 px-4 text-sm font-semibold",
            )}
          >
            View posting
            <ExternalLink aria-hidden="true" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        ) : null}
      </div>
    </article>
  );
}
