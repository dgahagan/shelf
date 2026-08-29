"""The provider outcome record, as a table.

Seven metadata clients used to answer with a bare value and report the
*quality* of that answer through a raise, a callback, or a log line. These
pins are the contract that replaced all three: what each constructor means,
that the record refuses to be truth-tested, how a raw response is classified,
and how a cascade folds.
"""

import typing

import httpx
import pytest

from app.services import provider_result as pr


class TestConstructors:
    def test_each_constructor_sets_its_own_outcome(self):
        assert pr.found("openlibrary", {"title": "Dune"}).outcome == "found"
        assert pr.no_match("openlibrary").outcome == "no_match"
        assert pr.no_credential("hardcover").outcome == "no_credential"
        assert pr.rejected("tmdb", status=401).outcome == "rejected"
        assert pr.rate_limited("google").outcome == "rate_limited"
        assert pr.transport_failed("upcitemdb").outcome == "transport_failed"

    def test_found_is_true_only_for_a_hit(self):
        assert pr.found("tmdb", {"title": "Alien"}).found is True
        for other in (
            pr.no_match("tmdb"),
            pr.no_credential("tmdb"),
            pr.rejected("tmdb", status=403),
            pr.rate_limited("tmdb"),
            pr.transport_failed("tmdb"),
        ):
            assert other.found is False

    def test_the_payload_and_provider_survive(self):
        result = pr.found("igdb", [{"name": "Portal"}])
        assert result.provider == "igdb"
        assert result.payload == [{"name": "Portal"}]
        assert result.status == 200

    def test_with_payload_keeps_outcome_provider_and_status(self):
        result = pr.found("igdb", [{"name": "Portal"}], status=200)
        unwrapped = result.with_payload({"name": "Portal"})
        assert unwrapped.outcome == "found"
        assert unwrapped.provider == "igdb"
        assert unwrapped.status == 200
        assert unwrapped.payload == {"name": "Portal"}

    def test_the_runtime_tuple_matches_the_literal(self):
        assert set(pr.OUTCOMES) == set(typing.get_args(pr.Outcome))
        assert len(pr.OUTCOMES) == len(typing.get_args(pr.Outcome))


class TestItRefusesToBeTruthTested:
    """Every pre-existing caller wrote `if metadata:`.

    A dataclass is always truthy, so a `no_match` record left in one of those
    branches would read as a hit and file a title with garbage metadata. The
    record raises instead, which turns that mistake into a loud failure at the
    one call site that made it.
    """

    def test_bool_raises_and_names_the_replacement(self):
        with pytest.raises(TypeError, match=r"\.found"):
            bool(pr.no_match("openlibrary"))

    def test_even_a_hit_raises(self):
        with pytest.raises(TypeError):
            bool(pr.found("openlibrary", {"title": "Dune"}))

    def test_not_raises_too(self):
        with pytest.raises(TypeError):
            not pr.no_match("openlibrary")


class TestClassifyResponse:
    def test_a_200_is_the_clients_own_business(self):
        assert pr.classify_response("openlibrary", httpx.Response(200)) is None

    def test_an_auth_status_is_rejected(self):
        result = pr.classify_response(
            "tmdb", httpx.Response(401), auth_statuses=(401, 403)
        )
        assert result.outcome == "rejected"
        assert result.status == 401

    def test_a_429_is_rate_limited(self):
        result = pr.classify_response("google", httpx.Response(429))
        assert result.outcome == "rate_limited"
        assert result.status == 429

    def test_auth_outranks_rate_limiting(self):
        """A status in both sets is reported as the one the user can fix.

        IGDB's auth set contains 400 and Google Books' quota answer is a 429,
        so the overlap is real rather than hypothetical; this pins the order
        `tmdb.py` applied when it still raised.
        """
        result = pr.classify_response(
            "igdb", httpx.Response(429), auth_statuses=(400, 401, 403, 429)
        )
        assert result.outcome == "rejected"

    @pytest.mark.parametrize("status", [400, 404, 500, 503])
    def test_any_other_non_200_is_a_miss(self, status):
        result = pr.classify_response("dnb", httpx.Response(status))
        assert result.outcome == "no_match"
        assert result.status == status

    def test_an_auth_status_not_declared_is_a_miss(self):
        """The auth set is per-provider; an undeclared 401 is not special."""
        assert pr.classify_response("dnb", httpx.Response(401)).outcome == "no_match"

    def test_a_503_is_not_a_spent_quota(self):
        """`is_rate_limited`, not `RETRY_STATUSES` — the two disagree here.

        503 is worth retrying but is not the provider telling the user to come
        back later, and telling them to is wrong (GOTCHAS G60).
        """
        assert pr.classify_response("google", httpx.Response(503)).outcome == "no_match"


class TestCombine:
    def test_a_hit_wins_and_keeps_its_own_provider(self):
        result = pr.combine(
            [pr.no_match("openlibrary"), pr.found("hardcover", {"title": "Dune"})],
            provider="isbn-cascade",
        )
        assert result.found
        assert result.provider == "hardcover"
        assert result.payload == {"title": "Dune"}

    def test_a_hit_beside_a_rejection_is_still_a_hit(self):
        """The found-wins hazard, pinned.

        `_scan_upc` must never fold its always-`found` product record in with
        the enrichment record: this rule would return the product and the card
        would call every TMDb rejection a clean hit. The rule itself is right;
        the comment in `items_common._scan_upc` points here.
        """
        result = pr.combine(
            [pr.found("upcitemdb", {"title": "Alien"}), pr.rejected("tmdb", status=401)],
            provider="tmdb",
        )
        assert result.found
        assert result.provider == "upcitemdb"

    def test_rejected_outranks_rate_limited(self):
        result = pr.combine(
            [pr.rate_limited("google"), pr.rejected("hardcover", status=401)],
            provider="isbn-cascade",
        )
        assert result.outcome == "rejected"

    def test_the_winning_failure_keeps_its_own_leg(self):
        """The card names the credential that was actually rejected.

        `fragments/scan_result.html`'s `rejected` arm interpolates a provider
        name; a synthesised `"isbn-cascade"` would leave a hole in the
        sentence, and the ISBN path is exactly where that arm became
        reachable.
        """
        result = pr.combine(
            [pr.no_match("openlibrary"), pr.rejected("google", status=400)],
            provider="isbn-cascade",
        )
        assert result.provider == "google"
        assert result.status == 400

    def test_rate_limited_outranks_transport_failed(self):
        result = pr.combine(
            [pr.transport_failed("dnb"), pr.rate_limited("google")],
            provider="isbn-cascade",
        )
        assert result.outcome == "rate_limited"

    def test_transport_failed_outranks_no_match(self):
        result = pr.combine(
            [pr.no_match("openlibrary"), pr.transport_failed("dnb")],
            provider="isbn-cascade",
        )
        assert result.outcome == "transport_failed"

    def test_no_match_outranks_no_credential(self):
        """One leg that was asked and missed speaks over one never asked."""
        result = pr.combine(
            [pr.no_credential("hardcover"), pr.no_match("openlibrary")],
            provider="isbn-cascade",
        )
        assert result.outcome == "no_match"

    def test_no_credential_only_when_every_leg_was(self):
        result = pr.combine(
            [pr.no_credential("hardcover"), pr.no_credential("google")],
            provider="isbn-cascade",
        )
        assert result.outcome == "no_credential"
        assert result.provider == "hardcover"

    def test_an_empty_cascade_is_a_miss(self):
        result = pr.combine([], provider="isbn-cascade")
        assert result.outcome == "no_match"
        assert result.provider == "isbn-cascade"
