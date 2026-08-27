"""Which dead end a scan hit, as one state name.

The scan card tells the user *why* enrichment did not happen — the credential
is missing, the credential was rejected, the provider is rate-limiting us,
Shelf has no metadata source for this format, or the provider genuinely had no
match. Issues #42 and #44 were both cases of that answer being wrong: a
rejected Twitch credential rendered as "no match", and a CD rendered as "no
TMDb match" after a real request to a film database.

**This module decides; the template says it.** Every function here returns a
bare state name, never markup and never a sentence. The copy and the Settings
anchor live in `fragments/scan_result.html`, so nothing on that card is ever
rendered `|safe` — it also renders a title that came off a scanned barcode,
and a notice assembled in Python would be one interpolation away from stored
XSS (GOTCHAS G58).

It lives beside the services rather than in `items_common` because it is a
decision about what the providers reported, not about routing a request — and
because both UPC branches need the same decision. They had two near-identical
copies of this ladder before, which is exactly how the film branch and the
game branch drifted apart in the first place.
"""

# The states `fragments/scan_result.html` has an arm for. A value not in here
# renders nothing under the notice block, which is a silent no-op rather than
# an error — so this tuple is the contract, and the test that pins it against
# the template is what keeps the two in step.
ENRICH_STATES = ("no_credential", "rejected", "quota", "no_provider", "no_match")


def enrich_status(
    *,
    found: bool,
    has_credential: bool,
    auth_rejected: bool = False,
    rate_limited: bool = False,
    has_provider: bool = True,
) -> str | None:
    """The one reason a scan filed a title without metadata, or `None`.

    `None` means enrichment succeeded — the caller has real metadata and the
    card shows no notice at all.

    Precedence, and why it is this way round:

    1. **`no_provider`** outranks everything, because it is the only one that
       is true *before* any request is made. Shelf never asked, so nothing it
       could have been told applies. This is #44: a CD has no metadata source,
       and saying "no TMDb match" for one names a provider that was never
       going to have it.
    2. **`no_credential`** — nothing was asked because nothing could be.
    3. **`rejected`** outranks `quota`: a rejected credential is the one the
       user can actually act on, so a scan that saw both says so.
    4. **`quota`** — the provider refused for rate reasons, so this may not be
       a genuine miss.
    5. **`no_match`** — the honest default. The provider was asked and had
       nothing.

    Keyword-only on purpose: five booleans in a row is exactly the shape that
    gets silently transposed at a call site, and both UPC branches call it.
    """
    if not has_provider:
        return "no_provider"
    if found:
        return None
    if not has_credential:
        return "no_credential"
    if auth_rejected:
        return "rejected"
    if rate_limited:
        return "quota"
    return "no_match"
