"""Internshala adapter: listing-card scraper under the D-15 rules (ING-04, ING-08, ING-12).

Only ``/jobs/<category>-jobs/`` and ``/internships/<category>-internship/`` listing pages (and
their ``/page-N/``) are fetched, always through :class:`PoliteFetcher` (runtime robots check that
fails closed, 3 s spacing, no retry after a refusal). Detail pages are never fetched: the absolute
detail URL on each card is stored for attribution and nothing else is read from it. Recruiter
contact details are never kept; ``description_raw`` is built only from stable card fields so the
content hash does not change when the "3 days ago" label ticks over.
"""

import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlsplit

import structlog
from pydantic import Field, ValidationError

from ranker.ingest.http import build_client
from ranker.ingest.polite import FetchRefused, PoliteFetcher, RobotsDisallowed
from ranker.ingest.sources import apiutil
from ranker.ingest.sources.base import (
    AdapterContext,
    BaseSourceConfig,
    FetchResult,
    RawJob,
    SourceAdapter,
    register,
)

log = structlog.get_logger("ranker.ingest.internshala")

BASE_URL = "https://internshala.com"
CARD_CLASS = "individual_internship"
TOTAL_RE = re.compile(r"Find ([\d,]+) [A-Za-z ]*?(?:Vacancies|Internships?|Jobs)", re.I)
PAGE_RE = re.compile(r"/page-(\d+)")
PAGE_SIZE = 50
LISTING_PATH_RE = re.compile(
    r"^/(?:jobs/[a-z0-9-]+-jobs|internships/[a-z0-9-]+-internship)/(?:page-[0-9]+/)?$"
)
DETAIL_PATH_RE = re.compile(r"^/(?:job|internship)/detail/[A-Za-z0-9_-]*?(\d+)$")
AGE_RE = re.compile(r"\b(?:ago|just now|today|few hours)\b", re.I)
AGE_NUM_RE = re.compile(r"(\d+)\+?\s*(hour|day|week|month)", re.I)
PART_TIME_RE = re.compile(r"part[\s-]?time", re.I)
WFH_RE = re.compile(r"work from home|remote|wfh", re.I)
VOID_TAGS = frozenset({"br", "img", "input", "hr", "meta", "link", "source", "wbr", "area", "col"})
FIELD_CAP = 300

Kind = Literal["jobs", "internships"]


DEFAULT_JOB_CATEGORIES = (
    "python-jobs",
    "software-development-jobs",
    "web-development-jobs",
    "data-science-jobs",
    "java-jobs",
)
DEFAULT_INTERNSHIP_CATEGORIES = (
    "python-internship",
    "web-development-internship",
    "software-development-internship",
    "data-science-internship",
    "machine-learning-internship",
)


class InternshalaConfig(BaseSourceConfig):
    base_url: str = BASE_URL
    job_categories: list[str] = Field(default_factory=lambda: list(DEFAULT_JOB_CATEGORIES))
    internship_categories: list[str] = Field(
        default_factory=lambda: list(DEFAULT_INTERNSHIP_CATEGORIES)
    )
    max_pages_per_category: int = 20
    min_interval_seconds: float = 3.0


def is_listing_path(target: str, base_url: str = BASE_URL) -> bool:
    """True only for the allowed listing-page shapes on the configured host (D-15)."""
    if "?" in target or "," in target or "#" in target:
        return False
    if "://" in target:
        parts = urlsplit(target)
        base = urlsplit(base_url)
        if (parts.scheme, parts.netloc) != (base.scheme, base.netloc):
            return False
        target = parts.path
    return LISTING_PATH_RE.match(target) is not None


class Card:
    """Raw text fragments of one listing card, before mapping."""

    def __init__(self, data_href: str | None) -> None:
        self.data_href = data_href
        self.title_href: str | None = None
        self.fields: dict[str, list[list[str]]] = {}
        self.texts: list[str] = []

    def add_buffer(self, name: str) -> list[str]:
        buf: list[str] = []
        self.fields.setdefault(name, []).append(buf)
        return buf

    def joined(self, name: str, sep: str = " ") -> str:
        out: list[str] = []
        for buf in self.fields.get(name, []):
            for piece in buf:
                if piece not in out:
                    out.append(piece)
        return sep.join(out)


_MARKERS = ("job-title-href", "company-name", "locations", "job_skill")
_ICONS = {"ic-16-money": "salary", "ic-16-briefcase": "experience"}


class _Entry:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.buffers: list[list[str]] = []
        self.is_card = False


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[Card] = []
        self._stack: list[_Entry] = []
        self._card: Card | None = None

    def _close_card(self) -> None:
        if self._card is not None:
            self.cards.append(self._card)
        self._card = None
        while self._stack and not self._stack[-1].is_card:
            self._stack.pop()
        if self._stack:
            self._stack.pop()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        classes = attr.get("class", "").split()
        if CARD_CLASS in classes and "container-fluid" in classes:
            if self._card is not None:
                self._close_card()
            self._card = Card(attr.get("data-href") or None)
            entry = _Entry(tag)
            entry.is_card = True
            self._stack.append(entry)
            return
        if self._card is None:
            return
        if tag in VOID_TAGS:
            self._icon(classes)
            return
        entry = _Entry(tag)
        for marker in _MARKERS:
            if marker in classes:
                entry.buffers.append(self._card.add_buffer(marker))
        if "job-title-href" in classes and attr.get("href"):
            self._card.title_href = attr["href"]
        if not self._card.data_href and attr.get("data-href"):
            self._card.data_href = attr["data-href"]
        self._icon(classes)  # before the push: the icon's parent is the current top of stack
        self._stack.append(entry)

    def _icon(self, classes: list[str]) -> None:
        if self._card is None:
            return
        for icon, name in _ICONS.items():
            if icon in classes:
                # the icon's text lives in its container, so the container (parent) captures it
                parent = self._stack[-1] if self._stack else None
                if parent is not None and not parent.is_card:
                    parent.buffers.append(self._card.add_buffer(name))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._card is None:
            return
        classes = dict((k, v or "") for k, v in attrs).get("class", "").split()
        self._icon(classes)

    def handle_endtag(self, tag: str) -> None:
        if self._card is None or tag in VOID_TAGS:
            return
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx].tag == tag:
                closing_card = self._stack[idx].is_card
                del self._stack[idx:]
                if closing_card:
                    self.cards.append(self._card)
                    self._card = None
                return

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return
        text = " ".join(data.split())
        if not text or not any(ch.isalnum() or ch in "₹$€£" for ch in text):
            return
        self._card.texts.append(text)
        for entry in self._stack:
            for buf in entry.buffers:
                buf.append(text)

    def close(self) -> None:
        super().close()
        if self._card is not None:
            self.cards.append(self._card)
            self._card = None


class ParsedCard:
    def __init__(
        self,
        *,
        source_job_id: str,
        url: str,
        title: str,
        company: str,
        locations: list[str],
        salary: str | None,
        experience: str | None,
        skills: list[str],
        posted_age: str | None,
        part_time: bool,
    ) -> None:
        self.source_job_id = source_job_id
        self.url = url
        self.title = title
        self.company = company
        self.locations = locations
        self.salary = salary
        self.experience = experience
        self.skills = skills
        self.posted_age = posted_age
        self.part_time = part_time


class ListingPage:
    def __init__(self) -> None:
        self.cards: list[ParsedCard] = []
        self.invalid = 0
        self.pages_available: int | None = None
        self.total_listed: int | None = None


def _detail(card: Card, base_url: str) -> tuple[str, str] | None:
    raw = card.data_href or card.title_href
    if not raw:
        return None
    path = urlsplit(raw).path
    m = DETAIL_PATH_RE.match(path)
    if m is None:
        return None
    return m.group(1), f"{base_url.rstrip('/')}{path}"


def parse_listing_page(html: str, kind: Kind, base_url: str = BASE_URL) -> ListingPage:
    """Parse one listing page. Promoted blocks without a detail link are skipped, not invalid."""
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    page = ListingPage()
    for card in parser.cards:
        found = _detail(card, base_url)
        if found is None:
            if card.data_href or card.title_href:
                page.invalid += 1  # has a link, but not a detail-path shape
            continue
        title = card.joined("job-title-href")[:FIELD_CAP]
        company = card.joined("company-name")[:FIELD_CAP]
        if not title or not company:
            page.invalid += 1
            continue
        locations = [p for buf in card.fields.get("locations", []) for p in buf if p.strip(" ,")]
        age = next((t for t in card.texts if AGE_RE.search(t) and len(t) <= 40), None)
        skills: list[str] = []
        for buf in card.fields.get("job_skill", []):
            skill = " ".join(buf)[:100]
            if skill and skill not in skills:
                skills.append(skill)
        salary = card.joined("salary")[:FIELD_CAP] or None
        experience = (card.joined("experience")[:FIELD_CAP] or None) if kind == "jobs" else None
        page.cards.append(
            ParsedCard(
                source_job_id=found[0],
                url=found[1],
                title=title,
                company=company,
                locations=list(dict.fromkeys(locations))[:10],
                salary=salary,
                experience=experience,
                skills=skills[:30],
                posted_age=age,
                part_time=any(PART_TIME_RE.search(t) for t in card.texts),
            )
        )
    m = TOTAL_RE.search(html)
    if m:
        page.total_listed = int(m.group(1).replace(",", ""))
        page.pages_available = max(1, math.ceil(page.total_listed / PAGE_SIZE))
    else:
        nums = [int(p) for p in PAGE_RE.findall(html)]
        if nums:
            page.pages_available = max(nums)
    return page


def parse_age(text: str | None, now: datetime) -> datetime | None:
    """Turn a card's 'posted' label into a timestamp relative to the run time."""
    if not text:
        return None
    low = text.lower()
    if "just now" in low or "today" in low or "few hours" in low:
        return now
    m = AGE_NUM_RE.search(low)
    if m is None:
        return None
    n = min(int(m.group(1)), 365)
    unit = m.group(2)
    days = {"hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[unit]
    if "30+" in low:
        days = 30
    return now - timedelta(days=days)


def card_to_raw(card: ParsedCard, kind: Kind, category: str, now: datetime) -> RawJob:
    is_wfh = any(WFH_RE.search(loc) for loc in card.locations)
    places = [loc for loc in card.locations if not WFH_RE.search(loc)]
    lines = [card.title, card.company]
    if card.locations:
        lines.append("Location: " + ", ".join(card.locations))
    if card.experience:
        lines.append("Experience: " + card.experience)
    if card.salary:
        lines.append(("Stipend: " if kind == "internships" else "Salary: ") + card.salary)
    if card.skills:
        lines.append("Skills: " + ", ".join(card.skills))
    if kind == "internships":
        employment = "internship"
    else:
        employment = "part_time" if card.part_time else "full_time"
    has_amount = bool(card.salary and any(ch.isdigit() for ch in card.salary))
    return RawJob(
        source="internshala",
        source_job_id=card.source_job_id,
        source_scope=f"{kind}/{category}",
        url=card.url,  # pyright: ignore[reportArgumentType]
        title=card.title,
        company_name=card.company,
        description_raw="\n".join(lines),
        description_is_html=False,
        location_text=", ".join(places) or None,
        location_parts=places,
        country_hint="IN",
        region_hint="India",
        workplace_hint="remote" if is_wfh else None,
        posted_at=parse_age(card.posted_age, now),
        salary_text=card.salary if has_amount else None,
        salary_currency="INR" if has_amount else None,
        employment_type_hint=employment,
        skills_hint=card.skills,
    )


SleepFn = Callable[[float], Awaitable[None]]


class InternshalaAdapter:
    name = "internshala"
    expiry_policy: Literal["absence", "ttl"] = "absence"

    def __init__(self, ctx: AdapterContext, *, sleep: SleepFn | None = None) -> None:
        self._cfg = InternshalaConfig.model_validate(ctx.config.model_dump())
        self._now = ctx.now
        self._sleep: SleepFn = sleep or (lambda sec: apiutil.sleep(sec))
        self._result = FetchResult()

    def configured(self) -> bool:
        return True

    def _scopes(self) -> list[tuple[Kind, str]]:
        scopes: list[tuple[Kind, str]] = []
        for cat in self._cfg.job_categories:
            scopes.append(("jobs", cat))
        for cat in self._cfg.internship_categories:
            scopes.append(("internships", cat))
        return scopes

    def _path(self, kind: Kind, category: str, page: int) -> str:
        suffix = f"page-{page}/" if page > 1 else ""
        return f"/{kind}/{category}/{suffix}"

    async def fetch(self) -> AsyncIterator[RawJob]:
        self._result = FetchResult()
        seen: set[str] = set()
        scopes = self._scopes()
        refusal: str | None = None
        async with build_client() as client:
            fetcher = PoliteFetcher(
                client, min_interval_s=self._cfg.min_interval_seconds, sleep=self._sleep
            )
            try:
                for kind, category in scopes:
                    scope = f"{kind}/{category}"
                    if refusal is not None:
                        self._result.failed_scopes[scope] = "host_refused"
                        continue
                    page_no, last = 1, 1
                    count = 0
                    try:
                        while page_no <= last:
                            if page_no > self._cfg.max_pages_per_category:
                                self._result.failed_scopes[scope] = "page_cap"
                                break
                            path = self._path(kind, category, page_no)
                            if not is_listing_path(path, self._cfg.base_url):
                                self._result.failed_scopes[scope] = "path_not_allowed"
                                break
                            html = await fetcher.get_text(f"{self._cfg.base_url.rstrip('/')}{path}")
                            page = parse_listing_page(html, kind, self._cfg.base_url)
                            self._result.invalid_items += page.invalid
                            if page_no == 1 and page.pages_available:
                                last = page.pages_available
                            if not page.cards:
                                break
                            for card in page.cards:
                                if card.source_job_id in seen:
                                    continue
                                seen.add(card.source_job_id)
                                try:
                                    job = card_to_raw(card, kind, category, self._now)
                                except ValidationError, ValueError:
                                    self._result.invalid_items += 1
                                    continue
                                count += 1
                                yield job
                            page_no += 1
                    except (FetchRefused, RobotsDisallowed) as exc:
                        refusal = str(exc) if isinstance(exc, FetchRefused) else "RobotsDisallowed"
                        self._result.failed_scopes[scope] = refusal
                        log.warning("source_refused", source=self.name, reason=refusal)
                    self._result.scope_counts[scope] = count
                    if scope not in self._result.failed_scopes:
                        self._result.complete_scopes.add(scope)
            finally:
                self._result.calls_used = fetcher.requests_made

    def result(self) -> FetchResult:
        return self._result


def _factory(ctx: AdapterContext) -> SourceAdapter:
    return InternshalaAdapter(ctx)


register("internshala", _factory)
