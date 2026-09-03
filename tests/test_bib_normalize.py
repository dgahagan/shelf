"""Tests for app.services.bib_normalize — format-independent bibliographic
helpers shared by MARC21, flat-JSON and Dublin Core national-bibliography
providers. Pure logic, no app/db needed (see G14)."""

import unicodedata

from app.services.bib_normalize import (
    first_year,
    invert_name,
    leading_int,
    nfc,
    split_publication,
    split_title,
    strip_responsibility,
)


# --- nfc ---


class TestNfc:
    def test_nfd_input_comes_back_nfc_and_stripped(self):
        nfd = unicodedata.normalize("NFD", "  Köhlmeier  ")
        out = nfc(nfd)
        assert out == "Köhlmeier"
        assert unicodedata.is_normalized("NFC", out)

    def test_empty_returns_empty(self):
        assert nfc("") == ""

    def test_marc_nonsort_markers_stripped(self):
        # DNB brackets a title's article in U+0098 / U+009C; they survive NFC
        # and reached the database as "\x98Der\x9c Kontrabaß" (test-drive
        # Observation 1). The whole C1 block is dropped; C0 whitespace is not.
        assert nfc("\x98Der\x9c Kontrabaß") == "Der Kontrabaß"
        assert nfc("\tGoethe\n") == "Goethe"


# --- invert_name ---


class TestInvertName:
    def test_date_qualifier_stripped(self):
        assert invert_name("Roncaglia, Gino <1960-    >") == "Gino Roncaglia"

    def test_middle_initial_preserved(self):
        assert invert_name("Hawking, Stephen W.") == "Stephen W. Hawking"

    def test_nonsort_particle_markers_stripped(self):
        assert invert_name("Goethe, Johann Wolfgang \x98von\x9c") == "Johann Wolfgang von Goethe"

    def test_simple_lastname_firstname(self):
        assert invert_name("Mann, Thomas") == "Thomas Mann"

    def test_empty_given_name_falls_back_to_surname(self):
        assert invert_name("Mann,") == "Mann"

    def test_no_comma_falls_back_to_whole_string(self):
        assert invert_name("Cher") == "Cher"

    def test_empty_string(self):
        assert invert_name("") == ""

    def test_nfd_input_normalized_to_nfc(self):
        nfd = unicodedata.normalize("NFD", "Köhlmeier, Michael")
        out = invert_name(nfd)
        assert out == "Michael Köhlmeier"
        assert unicodedata.is_normalized("NFC", out)

    def test_explicit_nfd_literal_also_normalizes(self):
        # Same case, written as the explicit decomposed literal ("o" +
        # U+0308 combining diaeresis) so the pin does not depend on
        # normalize() to build its own input.
        out = invert_name("Ko\u0308hlmeier, Michael")
        assert out == "Michael Köhlmeier"
        assert unicodedata.is_normalized("NFC", out)

    def test_non_date_parenthetical_survives(self):
        # On the record: the digits requirement in _DATE_QUALIFIER is a
        # deliberate decision, not an oversight. An unconditional "(...)"
        # strip would also eat a parenthetical that is part of the name
        # itself (e.g. a pseudonym note) — only a qualifier containing a
        # 3-4 digit run (the date form SBN's autorePrincipale carries) is
        # stripped, so "(pseud.)" here must be preserved verbatim.
        assert invert_name("Ferrante, Elena (pseud.)") == "Elena (pseud.) Ferrante"


# --- strip_responsibility ---


class TestStripResponsibility:
    def test_strips_statement_of_responsibility(self):
        title = (
            "La quarta rivoluzione : sei lezioni sul futuro del libro / "
            "Gino Roncaglia"
        )
        assert (
            strip_responsibility(title)
            == "La quarta rivoluzione : sei lezioni sul futuro del libro"
        )

    def test_no_delimiter_unchanged(self):
        title = "La quarta rivoluzione : sei lezioni sul futuro del libro"
        assert strip_responsibility(title) == title

    def test_bare_slash_is_not_the_delimiter(self):
        title = "Repair and/or replace: a guide"
        assert strip_responsibility(title) == title

    def test_empty_string(self):
        assert strip_responsibility("") == ""


# --- split_title ---
#
# G31: this class must redden under two mutations of split_title, each
# pinned by a distinct row below.
#   1. partition(" : ") -> rpartition(" : ") must fail
#      test_two_colons_splits_at_first, since that row's expected split only
#      happens at the *first* ' : ' — rpartition would instead cut at the
#      second, leaving it inside the title half.
#   2. deleting the `strip_responsibility(s)` call (partitioning `s` itself)
#      must fail test_worked_example, since that row's " / Gino Roncaglia"
#      responsibility statement would otherwise survive inside the subtitle.


class TestSplitTitle:
    def test_worked_example(self):
        # The SBN `titolo` field: title, subtitle and statement-of-
        # responsibility in one ISBD string. Proves the responsibility
        # statement is dropped *before* the ' : ' split, not after.
        title = (
            "La quarta rivoluzione : sei lezioni sul futuro del libro / "
            "Gino Roncaglia"
        )
        assert split_title(title) == (
            "La quarta rivoluzione",
            "sei lezioni sul futuro del libro",
        )

    def test_no_colon_no_slash_unchanged(self):
        assert split_title("Il nome della rosa") == ("Il nome della rosa", None)

    def test_slash_no_colon_strips_responsibility_only(self):
        assert split_title("Il nome della rosa / Umberto Eco") == (
            "Il nome della rosa",
            None,
        )

    def test_two_colons_splits_at_first(self):
        # If this cut at the last ' : ' instead (like split_publication
        # does for its imprint), the subtitle would come back as "sotto
        # titolo" only, with "altro : sotto titolo" folded into the title.
        assert split_title("Titolo : sottotitolo : altro dettaglio") == (
            "Titolo",
            "sottotitolo : altro dettaglio",
        )

    def test_empty_string(self):
        assert split_title("") == ("", None)

    def test_nfd_input_comes_back_nfc(self):
        nfd = unicodedata.normalize(
            "NFD", "Köhlmeier : eine Einführung / Michael Köhlmeier"
        )
        title, subtitle = split_title(nfd)
        assert title == "Köhlmeier"
        assert subtitle == "eine Einführung"
        assert unicodedata.is_normalized("NFC", title)
        assert unicodedata.is_normalized("NFC", subtitle)

    def test_whitespace_only_subtitle_is_none_not_empty_string(self):
        # strip_responsibility right-strips via nfc() before this function
        # ever partitions, so the trailing "    " here is gone before ' : '
        # is searched for -- the string collapses to "Titolo :" (no space
        # after the colon), which no longer contains the exact 3-char ' : '
        # delimiter at all. That makes this the *no-delimiter* branch, not
        # the partitioned-then-empty one -- but it still pins the outward
        # contract this row is named for: subtitle comes back None, never
        # "", even though its would-be content was pure whitespace.
        assert split_title("Titolo :    ") == ("Titolo :", None)


# --- split_publication ---


class TestSplitPublication:
    def test_place_publisher_year(self):
        assert split_publication("Roma : GLF editori Laterza, 2010") == (
            "GLF editori Laterza",
            2010,
        )

    def test_place_publisher_no_year(self):
        assert split_publication("Torino : Einaudi") == ("Einaudi", None)

    def test_bracketed_year(self):
        assert split_publication("Milano : Mondadori, [2015]") == ("Mondadori", 2015)

    def test_cuts_at_first_year_not_last_comma(self):
        # If this cut at the last comma instead, the publisher would come
        # back as "Laterza, 2010" or the year as 2011 (from "stampa 2011").
        assert split_publication("Roma : Laterza, 2010, stampa 2011") == (
            "Laterza",
            2010,
        )

    def test_no_place_separator(self):
        assert split_publication("Einaudi, 1999") == ("Einaudi", 1999)

    def test_empty_string(self):
        assert split_publication("") == (None, None)

    def test_strips_copyright_glyph(self):
        # ISBN 9788832970944 (Danielewski, "Casa di foglie") — measured live
        # from SBN's `pubblicazione` field, which carries a trailing "©"
        # before the year on this Italian imprint.
        assert split_publication("Roma : 66thand2nd, ©2019") == (
            "66thand2nd",
            2019,
        )


# --- first_year ---


class TestFirstYear:
    def test_bracketed_year(self):
        assert first_year("[2018]") == 2018

    def test_bare_year(self):
        assert first_year("2023") == 2023

    def test_no_year(self):
        assert first_year("o.J.") is None

    def test_empty_string(self):
        assert first_year("") is None


# --- leading_int ---


class TestLeadingInt:
    def test_leading_int_with_unit(self):
        assert leading_int("252 Seiten") == 252

    def test_leading_int_abbreviated_unit(self):
        assert leading_int("758 S.") == 758

    def test_not_leading_returns_none(self):
        # The pattern anchors at the start (re.match, not re.search) — a
        # digit run that appears later in the string ("300" here) is not a
        # *leading* int, so this must be None, not 300.
        assert leading_int("ca. 300 S.") is None

    def test_empty_string(self):
        assert leading_int("") is None
