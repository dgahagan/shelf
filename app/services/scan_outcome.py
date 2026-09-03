"""Which dead end a scan hit, as one state name.

The scan card tells the user *why* enrichment did not happen — the credential
is missing, the credential was rejected, the provider is rate-limiting us,
Shelf has no metadata source for this format, or the provider genuinely had no
match. Issues #42 and #44 were both cases of that answer being wrong: a
rejected Twitch credential rendered as "no match", and a CD rendered as "no
TMDb match" after a real request to a film database.

**This module decides; the template says it.** Every function here returns a
bare state name or a bare provider label, never markup and never a sentence. The copy and the Settings
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

from app.services import provider_result

# The states `fragments/scan_result.html` has an arm for. A value not in here
# renders nothing under the notice block, which is a silent no-op rather than
# an error — so this tuple is the contract, and the test that pins it against
# the template is what keeps the two in step.
ENRICH_STATES = (
    # `enrich_status()` never returns this one. It means no provider was asked
    # at all — `enrich_status` is a projection over a `ProviderResult`, and a
    # hardware scan has no `ProviderResult` because nothing was asked. The
    # router assigns it directly (issue #43).
    "no_lookup",
    "no_credential",
    "rejected",
    "quota",
    "offline",
    "no_provider",
    "no_match",
)

# `fragments/scan_result.html` is two cards, not one, and their arm sets differ.
# A state with no arm in the branch that was reached renders *nothing* —
# silently. The template's own comments argue each omission; these tuples are
# what a test can check. G65.
NOT_FOUND_ARMS: tuple[str, ...] = ("rejected", "quota")
RESULT_CARD_ARMS: tuple[str, ...] = ENRICH_STATES

# How each `ProviderResult.provider` identifier is spelled on the card. The
# identifiers are display-free by design, so the mapping lives here beside the
# other card vocabulary rather than in seven clients. A provider absent from
# this map renders no name, which is what the cascade-wide states want.
PROVIDER_LABELS = {
    "openlibrary": "Open Library",
    "hardcover": "Hardcover",
    "google": "Google Books",
    "dnb": "DNB",
    "sbn": "SBN",
    "tmdb": "TMDb",
    "igdb": "IGDB",
    "upcitemdb": "UPC Item DB",
}


def enrich_status(
    result: provider_result.ProviderResult, *, has_provider: bool = True
) -> str | None:
    """The one reason a scan filed a title without metadata, or `None`.

    A **projection** over what the provider actually reported, not a
    reassembly from flags the caller had to keep in step. That is the whole
    point: five booleans could only be built where all five were in scope, so
    the branches that had only some of them wrote the answer out by hand and
    drifted (issues #42, #44, #45, #47).

    `None` means enrichment succeeded — the caller has real metadata and the
    card shows no notice at all.

    Precedence, and why it is this way round. (`no_lookup` is not on this
    list: it is never returned here, only assigned directly by the router for
    a scan that never reached this function at all — see `ENRICH_STATES`.)

    1. **`no_provider`** outranks everything, because it is the only one that
       is true *before* any request is made. Shelf never asked, so nothing it
       could have been told applies. This is #44: a CD has no metadata source,
       and saying "no TMDb match" for one names a provider that was never
       going to have it.
    2. **`found`** — nothing to explain.
    3. **`no_credential`** — nothing was asked because nothing could be.
    4. **`rejected`** ranks above `rate_limited`: a rejected credential is the
       one the user can actually act on, so a scan that saw both says so. The
       record carries only one outcome, and `provider_result.combine` applies
       exactly this order when a cascade saw several.
    5. **`rate_limited`** → `"quota"` — the provider refused for rate reasons,
       so this may not be a genuine miss.
    6. **`transport_failed`** → `"offline"` — Shelf could not reach the
       provider at all. It ranks below `quota` because a refusal the provider
       *sent* is a stronger statement than one it never answered.
    7. **`no_match`** — the honest default. The provider was asked and had
       nothing.

    `transport_failed` used to answer `"no_match"` here. It does not any more,
    and the reason the old answer looked right is worth keeping: on the scan
    card the router chooses the connectivity card (a `status` of `error`)
    *before* this function is reached whenever the product or cascade lookup
    is what failed, so what still arrives here is a transport failure on the
    **enrichment** leg of an item that was filed anyway — and "could not reach
    TMDb" is a truer thing to tell that user than "no match". The search
    surfaces added by issue #49 have no connectivity card at all, so for them
    `"offline"` is the only way the state can be said.
    """
    if not has_provider:
        return "no_provider"
    if result.found:
        return None
    if result.outcome == "no_credential":
        return "no_credential"
    if result.outcome == "rejected":
        return "rejected"
    if result.outcome == "rate_limited":
        return "quota"
    if result.outcome == "transport_failed":
        return "offline"
    return "no_match"


def not_found_status(
    result: provider_result.ProviderResult, *, has_provider: bool = True
) -> str | None:
    """`enrich_status` for a card whose own message already says "Not found".

    Identical, except that `no_match` becomes `None`. The three not-found
    branches would otherwise print "no <provider> match for this barcode"
    directly under "Not found — add manually below", saying the same thing
    twice in two vocabularies. Every *actionable* state still renders: a
    missing key, a rejected key, a spent quota and an unreachable provider all
    mean the miss may not be a real one, which is the whole reason these
    branches carry a notice. `offline` therefore passes straight through.
    """
    state = enrich_status(result, has_provider=has_provider)
    return None if state == "no_match" else state


def provider_label(result: provider_result.ProviderResult) -> str | None:
    """The provider name the card interpolates, or `None` if it has none.

    A bare label, never a sentence — `fragments/scan_result.html` writes the
    copy around it (G58). The UPC branches pass their own literal because each
    has exactly one enrichment provider; the ISBN cascade has four, so it
    reads the leg off the record `combine` returned.
    """
    return PROVIDER_LABELS.get(result.provider)
