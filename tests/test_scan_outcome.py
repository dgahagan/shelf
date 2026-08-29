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
    PROVIDER_LABELS,
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

    def test_a_transport_failure_reads_as_a_miss(self):
        """Deliberate: the connectivity card is a `status`, decided upstream.

        By the time this function sees a `transport_failed` record the router
        has already chosen to file the item rather than render the error card,
        so the notice has nothing better to say than "no match".
        """
        assert enrich_status(pr.transport_failed("dnb")) == "no_match"

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

    def test_has_provider_is_keyword_only(self):
        """The one remaining flag must not be positionally transposable."""
        with pytest.raises(TypeError):
            enrich_status(pr.no_match("tmdb"), False)  # type: ignore[misc]


class TestNotFoundStatus:
    """The variant for a card whose own message already says "Not found"."""

    def test_a_miss_says_nothing_twice(self):
        assert not_found_status(pr.no_match("openlibrary")) is None

    def test_a_transport_failure_is_also_silent(self):
        assert not_found_status(pr.transport_failed("dnb")) is None

    @pytest.mark.parametrize("result, state", [
        (pr.rejected("google", status=400), "rejected"),
        (pr.rate_limited("google"), "quota"),
        (pr.no_credential("hardcover"), "no_credential"),
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

    def test_a_cascade_wide_identifier_has_none(self):
        """`combine` only stamps its own name on an empty cascade, which names nobody."""
        assert provider_label(pr.combine([], provider="isbn-cascade")) is None

    def test_every_label_is_a_bare_name(self):
        """G58: a label, never a sentence — the template writes the copy."""
        for label in PROVIDER_LABELS.values():
            assert "<" not in label and "." not in label


def test_every_declared_state_has_a_template_arm():
    """The contract that keeps the module and the card in step.

    A state the template has no arm for renders *nothing* under the notice
    block — a silent no-op, not an error. So a new state added here without a
    Jinja arm would ship as an invisible card, and only this pin would say so.
    """
    template = (
        Path(__file__).resolve().parents[1]
        / "app/templates/fragments/scan_result.html"
    ).read_text()
    arms = set(re.findall(r"enrich_status == '([a-z_]+)'", template))
    missing = set(ENRICH_STATES) - arms
    assert not missing, f"states with no template arm: {sorted(missing)}"
