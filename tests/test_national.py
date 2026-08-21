"""Tests for app.services.national — prefix-provider registry and the shared
MARC <-> ISO 639-1 language mapper. Pure logic, no app/db needed (see G14)."""

from app.services import dnb
from app.services.national import (
    PREFIX_PROVIDERS,
    SEARCH_LANGS,
    iso_to_marc,
    provider_for,
    to_iso639_1,
)


# --- provider_for ---


class TestProviderFor:
    def test_german_group_returns_dnb(self):
        assert provider_for("9783161484100") is dnb

    def test_english_group_returns_none(self):
        assert provider_for("9780134685991") is None

    def test_empty_returns_none(self):
        assert provider_for("") is None

    def test_none_like_returns_none(self):
        assert provider_for(None) is None  # type: ignore[arg-type]

    def test_longest_prefix_wins(self):
        # Register a narrower 5-digit sub-key alongside the existing 4-digit
        # "9783" key and confirm the longer, more specific key wins.
        sentinel = object()
        PREFIX_PROVIDERS["97831"] = sentinel  # type: ignore[assignment]
        try:
            assert provider_for("9783161484100") is sentinel
            # A prefix that only matches the shorter key still resolves.
            assert provider_for("9783400000000") is dnb
        finally:
            del PREFIX_PROVIDERS["97831"]


# --- to_iso639_1 ---


class TestToIso639_1:
    def test_marc_ger_deu(self):
        assert to_iso639_1("ger") == "de"
        assert to_iso639_1("deu") == "de"

    def test_marc_eng(self):
        assert to_iso639_1("eng") == "en"

    def test_marc_fre_fra(self):
        assert to_iso639_1("fre") == "fr"
        assert to_iso639_1("fra") == "fr"

    def test_marc_spa(self):
        assert to_iso639_1("spa") == "es"

    def test_marc_ita(self):
        assert to_iso639_1("ita") == "it"

    def test_marc_dut_nld(self):
        assert to_iso639_1("dut") == "nl"
        assert to_iso639_1("nld") == "nl"

    def test_marc_por(self):
        assert to_iso639_1("por") == "pt"

    def test_marc_swe(self):
        assert to_iso639_1("swe") == "sv"

    def test_marc_dan(self):
        assert to_iso639_1("dan") == "da"

    def test_marc_jpn(self):
        assert to_iso639_1("jpn") == "ja"

    def test_marc_rus(self):
        assert to_iso639_1("rus") == "ru"

    def test_marc_chi_zho(self):
        assert to_iso639_1("chi") == "zh"
        assert to_iso639_1("zho") == "zh"

    def test_marc_kor(self):
        assert to_iso639_1("kor") == "ko"

    def test_marc_pol(self):
        assert to_iso639_1("pol") == "pl"

    def test_marc_cze_ces(self):
        assert to_iso639_1("cze") == "cs"
        assert to_iso639_1("ces") == "cs"

    def test_marc_nor(self):
        assert to_iso639_1("nor") == "no"

    def test_openlibrary_languages_prefix_strip(self):
        assert to_iso639_1("/languages/ger") == "de"
        assert to_iso639_1("/languages/eng") == "en"

    def test_bcp47_subtag(self):
        assert to_iso639_1("de-DE") == "de"
        assert to_iso639_1("en-US") == "en"

    def test_plain_two_letter_passthrough(self):
        assert to_iso639_1("de") == "de"

    def test_unknown_code_passthrough_lowercased(self):
        assert to_iso639_1("xyz") == "xyz"
        assert to_iso639_1("XYZ") == "xyz"

    def test_none_returns_none(self):
        assert to_iso639_1(None) is None

    def test_empty_returns_none(self):
        assert to_iso639_1("") is None


# --- iso_to_marc ---


class TestIsoToMarc:
    def test_all_search_langs_never_keyerror(self):
        for code in SEARCH_LANGS:
            # Must not raise; result may be None for codes with no MARC form
            # such as "no" if unmapped, but must not blow up.
            iso_to_marc(code)

    def test_de_maps_to_ger_first(self):
        assert iso_to_marc("de") == "ger"

    def test_fr_maps_to_fre_first(self):
        assert iso_to_marc("fr") == "fre"

    def test_nl_maps_to_dut_first(self):
        assert iso_to_marc("nl") == "dut"

    def test_zh_maps_to_chi_first(self):
        assert iso_to_marc("zh") == "chi"

    def test_cs_maps_to_cze_first(self):
        assert iso_to_marc("cs") == "cze"

    def test_unknown_returns_none(self):
        assert iso_to_marc("xx") is None

    def test_none_returns_none(self):
        assert iso_to_marc(None) is None

    def test_all_16_search_langs_have_marc_forms(self):
        # Every SEARCH_LANGS code corresponds to a MARC code used in the
        # forward mapping table, so the reverse lookup should resolve for
        # all 16 (no dead entries in the dropdown).
        for code in SEARCH_LANGS:
            assert iso_to_marc(code) is not None, f"{code} has no MARC form"


# --- SEARCH_LANGS ---


class TestSearchLangs:
    def test_expected_codes_and_count(self):
        expected = [
            "en", "de", "fr", "es", "it", "nl", "pt", "sv",
            "da", "no", "pl", "cs", "ja", "ru", "zh", "ko",
        ]
        assert list(SEARCH_LANGS.keys()) == expected

    def test_display_names(self):
        assert SEARCH_LANGS["en"] == "English"
        assert SEARCH_LANGS["de"] == "German"
        assert SEARCH_LANGS["fr"] == "French"
        assert SEARCH_LANGS["ja"] == "Japanese"
        assert SEARCH_LANGS["zh"] == "Chinese"

    def test_every_code_is_valid_iso639_1_form(self):
        for code in SEARCH_LANGS:
            assert len(code) == 2
            assert code == code.lower()
