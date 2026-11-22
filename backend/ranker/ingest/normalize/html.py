"""Description cleaning (NORM-01): untrusted HTML in, safe plain text out.

Pure module. Raw HTML is never needed for display; output is text with paragraph breaks.
Greenhouse ``content`` is entity-escaped HTML, so it is unescaped once before sanitising.
"""

import html as _html
import re

import nh3

MAX_INPUT_CHARS = 200_000

_BLOCK_CLOSE = re.compile(r"</\s*(?:p|div|h[1-6]|tr|ul|ol|table|section|article)\b[^>]*>", re.I)
_BLOCK_OPEN = re.compile(r"<\s*(?:p|div|h[1-6]|tr|ul|ol|table|section|article)\b[^>]*>", re.I)
_BREAK = re.compile(r"<\s*br\b[^>]*/?>", re.I)
_LI_OPEN = re.compile(r"<\s*li\b[^>]*>", re.I)
_HSPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def _normalise(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    text = _HSPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def clean_description(raw: str, *, is_html: bool, entity_escaped: bool = False) -> str:
    """Return clean text for ``raw``.

    ``entity_escaped`` marks HTML whose tags arrive as ``&lt;p&gt;`` (Greenhouse).
    """
    text = raw[:MAX_INPUT_CHARS]
    if not is_html:
        return _normalise(_html.unescape(text))
    if entity_escaped:
        text = _html.unescape(text)
    text = _BREAK.sub("\n", text)
    text = _BLOCK_CLOSE.sub("\n\n", text)
    text = _BLOCK_OPEN.sub("\n\n", text)
    text = _LI_OPEN.sub("\n- ", text)
    text = nh3.clean(text, tags=set(), clean_content_tags={"script", "style"})
    return _normalise(_html.unescape(text))
