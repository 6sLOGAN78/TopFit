import pytest
from hypothesis import given
from hypothesis import strategies as st

from ranker.ingest.normalize.html import clean_description


def test_blocks_and_lists() -> None:
    raw = "<p>Hello <b>world</b></p><ul><li>Python</li><li>Go</li></ul>"
    assert clean_description(raw, is_html=True) == "Hello world\n\n- Python\n- Go"


def test_greenhouse_double_escape() -> None:
    raw = "&lt;p&gt;We use &amp;lt;C++&amp;gt;&lt;/p&gt;"
    out = clean_description(raw, is_html=True, entity_escaped=True)
    assert out == "We use <C++>"
    assert "<p>" not in out


def test_script_and_style_removed() -> None:
    raw = "<p>Hi</p><script>alert(1)</script><style>p{color:red}</style><p>there</p>"
    out = clean_description(raw, is_html=True)
    assert "alert" not in out
    assert "color" not in out
    assert out == "Hi\n\nthere"


def test_escaped_script_removed() -> None:
    raw = "&lt;p&gt;ok&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
    assert clean_description(raw, is_html=True, entity_escaped=True) == "ok"


def test_plain_text_only_whitespace_and_one_unescape() -> None:
    assert clean_description("  R&amp;D   team \n\n\n\n lead ", is_html=False) == "R&D team\n\nlead"


def test_plain_text_tags_untouched() -> None:
    assert clean_description("use <b> carefully", is_html=False) == "use <b> carefully"


def test_blank_line_collapse_and_strip() -> None:
    raw = "<div>a</div><div></div><div></div><div>b</div>"
    assert clean_description(raw, is_html=True) == "a\n\nb"


def test_nbsp_becomes_space() -> None:
    assert clean_description("a&nbsp;b\xa0c", is_html=True) == "a b c"


def test_br_becomes_newline() -> None:
    assert clean_description("line1<br>line2<br/>line3", is_html=True) == "line1\nline2\nline3"


def test_attributes_and_links_stripped() -> None:
    raw = '<a href="javascript:alert(1)" onclick="x()">Apply</a> <img src=x onerror=alert(1)>'
    out = clean_description(raw, is_html=True)
    assert out == "Apply"


def test_entities_after_sanitising() -> None:
    assert (
        clean_description("<p>Tom &amp; Jerry &copy; 2026</p>", is_html=True)
        == "Tom & Jerry © 2026"
    )


def test_headings_and_tables() -> None:
    raw = "<h2>Role</h2><table><tr><td>A</td></tr></table><p>end</p>"
    out = clean_description(raw, is_html=True)
    assert out.startswith("Role")
    assert out.endswith("end")
    assert "<" not in out


def test_input_capped() -> None:
    out = clean_description("a" * 300_000, is_html=True)
    assert len(out) == 200_000


def test_empty() -> None:
    assert clean_description("", is_html=True) == ""
    assert clean_description("   ", is_html=False) == ""


@given(st.text(alphabet=st.characters(blacklist_characters="<>&", blacklist_categories=["Cs"])))
def test_text_without_markup_is_stable(raw: str) -> None:
    once = clean_description(raw, is_html=True)
    assert clean_description(once, is_html=True) == once
    assert "\n\n\n" not in once
    assert once == once.strip()


@given(st.text())
def test_never_raises_and_no_nbsp(raw: str) -> None:
    out = clean_description(raw, is_html=True)
    assert "\xa0" not in out
    assert "<script" not in out.lower() or "<script" in raw.lower()


@pytest.mark.parametrize("tag", ["script", "style"])
def test_unclosed_dangerous_tag_content_dropped(tag: str) -> None:
    out = clean_description(f"<p>safe</p><{tag}>evil()", is_html=True)
    assert "evil" not in out
