"""The scan-outcome decision, as a table (issues #42, #44).

Both UPC branches used to carry their own near-identical copy of this ladder,
which is how the film branch came to make four distinctions while the game
branch made two. One function makes the decision now; these pins are the
precedence table it promises, asserted directly rather than through a rendered
card, so a future reader can see the ranking without reading Jinja.
"""

import re
from pathlib import Path

import pytest

from app.services.scan_outcome import ENRICH_STATES, enrich_status


class TestPrecedence:
    def test_a_hit_has_no_notice(self):
        assert enrich_status(found=True, has_credential=True) is None

    def test_no_provider_outranks_everything_including_a_hit(self):
        """It is the only state true *before* any request is made.

        Shelf never asked, so nothing it could have been told applies. This is
        #44: a CD has no metadata source, and "no TMDb match" for one names a
        provider that was never going to have it.
        """
        assert enrich_status(
            found=True, has_credential=True, auth_rejected=True,
            rate_limited=True, has_provider=False,
        ) == "no_provider"

    def test_a_missing_credential_beats_a_miss(self):
        assert enrich_status(found=False, has_credential=False) == "no_credential"

    def test_rejected_outranks_quota(self):
        """The one the user can act on wins when a scan saw both."""
        assert enrich_status(
            found=False, has_credential=True, auth_rejected=True, rate_limited=True,
        ) == "rejected"

    def test_quota_beats_a_bare_miss(self):
        assert enrich_status(
            found=False, has_credential=True, rate_limited=True,
        ) == "quota"

    def test_no_match_is_the_default(self):
        assert enrich_status(found=False, has_credential=True) == "no_match"

    def test_every_returned_state_is_declared(self):
        """Nothing can be returned that `ENRICH_STATES` does not list."""
        seen = set()
        for found in (True, False):
            for cred in (True, False):
                for rej in (True, False):
                    for rl in (True, False):
                        for prov in (True, False):
                            got = enrich_status(
                                found=found, has_credential=cred, auth_rejected=rej,
                                rate_limited=rl, has_provider=prov,
                            )
                            if got is not None:
                                seen.add(got)
        assert seen <= set(ENRICH_STATES)

    def test_the_signature_is_keyword_only(self):
        """Five booleans in a row is the shape that gets silently transposed."""
        with pytest.raises(TypeError):
            enrich_status(False, True)  # type: ignore[misc]


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
