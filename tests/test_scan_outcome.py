"""The scan-outcome decision, as a table (issues #42, #44).

Both UPC branches used to carry their own near-identical copy of this ladder,
which is how the film branch came to make four distinctions while the game
branch made two. One function makes the decision now; these pins are the
precedence table it promises, asserted directly rather than through a rendered
card, so a future reader can see the ranking without reading Jinja.

Rewritten against `ProviderResult`: the five hand-maintained booleans are gone
and this is now a *projection* over what the provider actually reported, which
is what lets the ISBN path call it at all.
"""

import re
from pathlib import Path

import pytest

from app.services import provider_result as pr
from app.services.scan_outcome import (
    ENRICH_STATES,
    NOT_FOUND_ARMS,
    PROVIDER_LABELS,
    RESULT_CARD_ARMS,
    enrich_status,
    not_found_status,
    provider_label,
)


class TestPrecedence:
    def test_a_hit_has_no_notice(self):
        assert enrich_status(pr.found("tmdb", {"title": "Alien"})) is None

    def test_no_provider_outranks_everything_including_a_hit(self):
        """It is the only state true *before* any request is made.

        Shelf never asked, so nothing it could have been told applies. This is
        #44: a CD has no metadata source, and "no TMDb match" for one names a
        provider that was never going to have it.
        """
        assert enrich_status(
            pr.found("tmdb", {"title": "Alien"}), has_provider=False
        ) == "no_provider"

    def test_a_missing_credential_beats_a_miss(self):
        assert enrich_status(pr.no_credential("tmdb")) == "no_credential"

    def test_rejected_is_its_own_state(self):
        assert enrich_status(pr.rejected("tmdb", status=401)) == "rejected"

    def test_rejected_outranks_quota_through_combine(self):
        """The one the user can act on wins when a cascade saw both.

        One record carries one outcome, so the precedence that used to live in
        two of this function's `if` arms now lives in `combine` — asserted
        here end to end so moving it did not lose it.
        """
        cascade = pr.combine(
            [pr.rate_limited("google"), pr.rejected("hardcover", status=401)],
            provider="isbn-cascade",
        )
        assert enrich_status(cascade) == "rejected"

    def test_quota_beats_a_bare_miss_through_combine(self):
        cascade = pr.combine(
            [pr.no_match("openlibrary"), pr.rate_limited("google")],
            provider="isbn-cascade",
        )
        assert enrich_status(cascade) == "quota"

    def test_no_match_is_the_default(self):
        assert enrich_status(pr.no_match("tmdb")) == "no_match"

    def test_a_transport_failure_says_it_could_not_be_reached(self):
        """Issue #49 gave the state its own name; it used to answer "no_match".

        By the time this function sees a `transport_failed` record the router
        has already chosen to file the item rather than render the error card
        — but "could not reach DNB" is a truer thing to tell that user than
        "no match", and the search surfaces have no connectivity card at all.
        """
        assert enrich_status(pr.transport_failed("dnb")) == "offline"

    def test_every_returned_state_is_declared(self):
        """Nothing can be returned that `ENRICH_STATES` does not list."""
        seen = set()
        for outcome in pr.OUTCOMES:
            for prov in (True, False):
                got = enrich_status(
                    pr.ProviderResult(outcome, "tmdb"), has_provider=prov
                )
                if got is not None:
                    seen.add(got)
        assert seen <= set(ENRICH_STATES)

    def test_offline_sits_between_quota_and_no_provider(self):
        """The ladder's order is the tuple's order, and both are load-bearing.

        `offline` ranks below `quota` — a refusal the provider *sent* is a
        stronger statement than one it never answered — and above the two
        states that mean nothing was asked or nothing was found.
        """
        assert ENRICH_STATES.index("offline") == ENRICH_STATES.index("quota") + 1
        assert (
            ENRICH_STATES.index("offline") < ENRICH_STATES.index("no_provider")
        )

    def test_has_provider_is_keyword_only(self):
        """The one remaining flag must not be positionally transposable."""
        with pytest.raises(TypeError):
            enrich_status(pr.no_match("tmdb"), False)  # type: ignore[misc]

    def test_no_lookup_is_first_in_the_tuple(self):
        """Arm order in the template follows tuple order (issue #43).

        `no_lookup` is not a rank in this function's ladder — nothing here
        ever returns it — but its position in `ENRICH_STATES` still matters
        because the template's first-match `{% if %}`/`{% elif %}` chain is
        built in tuple order.
        """
        assert ENRICH_STATES[0] == "no_lookup"

    def test_no_lookup_is_never_returned(self):
        """`enrich_status` is a projection over a `ProviderResult`.

        A hardware scan has no `ProviderResult` — nothing was asked — so no
        outcome this function can be handed should ever come back as
        `no_lookup`. Only the router (T3) assigns that state directly.
        """
        for outcome in pr.OUTCOMES:
            for has_provider in (True, False):
                got = enrich_status(
                    pr.ProviderResult(outcome, "tmdb"), has_provider=has_provider
                )
                assert got != "no_lookup"


class TestNotFoundStatus:
    """The variant for a card whose own message already says "Not found"."""

    def test_a_miss_says_nothing_twice(self):
        assert not_found_status(pr.no_match("openlibrary")) is None

    def test_a_transport_failure_is_actionable_and_still_renders(self):
        """`offline` is not `no_match`, so it survives the not-found squelch."""
        assert not_found_status(pr.transport_failed("dnb")) == "offline"

    @pytest.mark.parametrize("result, state", [
        (pr.rejected("google", status=400), "rejected"),
        (pr.rate_limited("google"), "quota"),
        (pr.no_credential("hardcover"), "no_credential"),
        (pr.transport_failed("tmdb"), "offline"),
    ])
    def test_every_actionable_state_still_renders(self, result, state):
        """A missing, rejected or throttled key means the miss may not be real."""
        assert not_found_status(result) == state

    def test_no_provider_still_outranks(self):
        assert not_found_status(
            pr.no_match("tmdb"), has_provider=False
        ) == "no_provider"


class TestProviderLabel:
    def test_each_client_identifier_has_a_label(self):
        """The identifiers are display-free by design; this is where they get a name."""
        assert provider_label(pr.rejected("google", status=400)) == "Google Books"
        assert provider_label(pr.rejected("openlibrary", status=401)) == "Open Library"
        assert provider_label(pr.found("sbn", {"title": "Test"})) == "SBN"

    def test_a_cascade_wide_identifier_has_none(self):
        """`combine` only stamps its own name on an empty cascade, which names nobody."""
        assert provider_label(pr.combine([], provider="isbn-cascade")) is None

    def test_every_label_is_a_bare_name(self):
        """G58: a label, never a sentence — the template writes the copy."""
        for label in PROVIDER_LABELS.values():
            assert "<" not in label and "." not in label


class TestTemplateArmsPerBranch:
    """The contract that keeps the module and the card in step — per branch.

    A state the template has no arm for renders *nothing* under the notice
    block — a silent no-op, not an error. So a new state added here without a
    Jinja arm would ship as an invisible card, and only a pin would say so.

    `fragments/scan_result.html` is two cards, not one card with one notice
    slot: it opens on `{% if status == 'not_found' %}` over a whole card with
    its own, smaller arm set, and a single top-level `{% else %}` falls
    through to a second, larger card with a different arm set (G65). A state
    with no arm in the branch actually reached at render time renders
    nothing, regardless of whether some *other* branch happens to have an arm
    for it — so a whole-file regex that unions both branches together cannot
    see that half the contract is missing. This class checks each half on
    its own, against `NOT_FOUND_ARMS` and `RESULT_CARD_ARMS` respectively,
    with set equality rather than a subset check: an arm the tuple does not
    declare is the other half of the same defect, meaning the template grew
    copy nobody wrote down.

    G53: don't reproduce the raw comparison this splitter greps for inside a
    comment or docstring the same regex could read — say the state names in
    backticks instead, the way `scan_result.html`'s own comments do.
    """

    _TEMPLATE_PATH = (
        Path(__file__).resolve().parents[1]
        / "app/templates/fragments/scan_result.html"
    )
    _ARM_PATTERN = r"enrich_status == '([a-z_]+)'"

    @classmethod
    def _halves(cls) -> tuple[str, str]:
        """Split the template on its one top-level `{% else %}`.

        The test is `line.rstrip() == "{% else %}"`, and `rstrip` rather than
        `strip` is the whole point: only a tag at **column 0** is the card
        boundary. `strip()` would also match the nested `{% else %}` arms
        (ternaries, per-field notices) that sit indented inside both cards —
        four lines rather than one — and slice the file at the wrong place.
        Exactly
        one such line must exist: if the template ever grows a second card,
        this must fail loudly rather than silently mis-slice the file at the
        wrong line.
        """
        lines = cls._TEMPLATE_PATH.read_text().splitlines()
        boundaries = [i for i, line in enumerate(lines) if line.rstrip() == "{% else %}"]
        assert len(boundaries) == 1, (
            "expected exactly one top-level {% else %} splitting the two "
            f"cards in scan_result.html, found {len(boundaries)} at lines "
            f"{[b + 1 for b in boundaries]} — the branch split this test "
            "relies on may have changed shape"
        )
        boundary = boundaries[0]
        not_found_half = "\n".join(lines[:boundary])
        result_card_half = "\n".join(lines[boundary + 1:])
        return not_found_half, result_card_half

    def test_not_found_card_arms_match_declared(self):
        not_found_half, _ = self._halves()
        arms = set(re.findall(self._ARM_PATTERN, not_found_half))
        assert arms == set(NOT_FOUND_ARMS)

    def test_result_card_arms_match_declared(self):
        _, result_card_half = self._halves()
        arms = set(re.findall(self._ARM_PATTERN, result_card_half))
        assert arms == set(RESULT_CARD_ARMS)
