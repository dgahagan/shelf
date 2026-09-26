"""Every item cover in a template declares its shape (issue #119).

Music artwork is square. A cover shown in a forced box — `object-cover`, an
`aspect-[…]` utility, or a `.cover-card` — crops it unless the element that owns
the box carries the cover-shape attribute, which one CSS rule turns into a
square box. The survey that found this bug counted 18 such sites, each its own
class string, so the 19th would be written by copying whichever one a builder
found first. This guard makes it declare the attribute or be exempted here, by
path, with a reason.

Comments are stripped before scanning (G53), and the allowlist compares
repo-relative paths, never basenames (G88).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"

ATTR = "data-cover-shape"

# Repo-relative path → why a forced cover box there needs no shape.
ALLOWLIST = {
    "app/templates/fragments/book_search_results.html":
        "provider search result: the image is a provider URL, not a stored "
        "item cover, and no music provider feeds this list",
    "app/templates/fragments/game_search_results.html":
        "provider search result (IGDB): never a music cover",
    "app/templates/fragments/dvd_search_results.html":
        "provider search result (TMDb): never a music cover",
    "app/templates/fragments/periodical_assisted_results.html":
        "provider search result for periodicals: never a music cover",
    "app/templates/series.html":
        "series are book-family, and the page's SELECT carries no media_type",
    "app/templates/scan.html":
        "the camera overlay's image is width-only (its h-22 is not a built "
        "class) and its Alpine state carries no media type",
}

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_JINJA_CONSTRUCT = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_TAG = re.compile(r"<[a-zA-Z][^>]*>", re.S)
# A forced cover box: object-cover, an arbitrary aspect utility, or the
# .cover-card class as a whole token (not cover-card-something).
_BOX = re.compile(r"object-cover|aspect-\[|(?<![\w-])cover-card(?![\w-])")


def _blank(m):
    """Blank a comment, keeping its newlines so line numbers survive."""
    return "\n" * m.group(0).count("\n")


def _neutralise_jinja(m):
    """Hide `<`/`>` inside a Jinja construct so the tag regex cannot end on
    a comparison, while keeping the construct's text (a class built in Jinja
    still counts)."""
    return m.group(0).replace("<", " ").replace(">", " ")


def scan(text: str) -> list[tuple[int, str]]:
    """(line, tag) for every forced-cover-box tag without the attribute."""
    text = _HTML_COMMENT.sub(_blank, _JINJA_COMMENT.sub(_blank, text))
    text = _JINJA_CONSTRUCT.sub(_neutralise_jinja, text)
    hits = []
    for m in _TAG.finditer(text):
        tag = m.group(0)
        if _BOX.search(tag) and ATTR not in tag:
            line = text.count("\n", 0, m.start()) + 1
            hits.append((line, " ".join(tag.split())))
    return hits


def boxes(text: str) -> int:
    """How many forced-cover-box tags `text` holds, attribute or not."""
    text = _HTML_COMMENT.sub(_blank, _JINJA_COMMENT.sub(_blank, text))
    text = _JINJA_CONSTRUCT.sub(_neutralise_jinja, text)
    return sum(1 for m in _TAG.finditer(text) if _BOX.search(m.group(0)))


def offences(files) -> list[str]:
    """`path:line  tag` for every unexempted offence among `files`, which are
    (repo-relative path, text) pairs."""
    out = []
    for rel, text in files:
        if rel in ALLOWLIST:
            continue
        for line, tag in scan(text):
            out.append(f"{rel}:{line}  {tag}")
    return out


def _template_files():
    for path in sorted(TEMPLATES.rglob("*.html")):
        yield path.relative_to(ROOT).as_posix(), path.read_text()


def test_every_forced_cover_box_declares_its_shape():
    found = offences(_template_files())
    assert not found, (
        "These tags force a cover into a box (object-cover / aspect-[…] / "
        f".cover-card) but carry no {ATTR}, so a square music cover is "
        "cropped there. Add\n"
        f'    {ATTR}="{{{{ cover_shape(<row>.media_type) }}}}"\n'
        "to the element that owns the box, or — if it never shows a stored "
        "item cover — add its repo-relative path to ALLOWLIST with a reason:\n"
        + "\n".join(found)
    )


def test_the_guard_checks_a_real_number_of_tags():
    # A guard that matched nothing would pass forever. The survey found 18
    # sites (some carry two boxed tags); well under that means the pattern
    # broke, not that the sites went away.
    total = sum(boxes(text) for _, text in _template_files())
    assert total >= 18, total


@pytest.mark.parametrize("rel", sorted(ALLOWLIST))
def test_every_exemption_is_still_live(rel):
    """A stale exemption must fail, not silently exempt nothing."""
    path = ROOT / rel
    assert path.is_file(), f"{rel} is exempt but no longer exists"
    assert boxes(path.read_text()), (
        f"{rel} is exempt but no longer holds a forced cover box; "
        "drop it from ALLOWLIST"
    )


def test_exemption_is_by_path_not_basename():
    # G88: a same-named file elsewhere is not exempt.
    tag = '<img src="/x.jpg" class="w-20 h-30 object-cover">'
    found = offences([("app/templates/other/series.html", tag)])
    assert found and found[0].startswith("app/templates/other/series.html:1")


def test_a_hit_inside_a_comment_is_ignored():
    text = (
        '{# <img class="object-cover"> #}\n'
        '<!-- <div class="cover-card"> -->\n'
        '<img class="w-8 h-12 object-cover">\n'
    )
    # Only the live tag on line 3 counts, and its line number survives the
    # comment blanking.
    assert [line for line, _ in scan(text)] == [3]


def test_a_tag_split_across_lines_is_matched_whole():
    text = (
        '<img src="/a.jpg"\n'
        '     class="w-full aspect-[2/3] object-cover"\n'
        f'     {ATTR}="{{{{ cover_shape(media_type) }}}}">\n'
    )
    assert scan(text) == []


def test_a_jinja_comparison_inside_a_tag_does_not_end_it():
    text = '<img class="{{ \'w-8\' if n > 1 else \'w-9\' }} object-cover">'
    assert [line for line, _ in scan(text)] == [1]


def test_cover_card_is_matched_as_a_whole_token_only():
    assert scan('<div class="cover-card-footer">') == []
    assert scan('<div class="x cover-card y">') != []
