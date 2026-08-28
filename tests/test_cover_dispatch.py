"""The cover picker dispatches on media type (plan: cover-sources-media, T3).

`search_covers` is the seam: `dvd` reaches TMDb's poster set, `video_game`
reaches IGDB cover art and artwork, and **everything else** — including an
unrecognised string, `None` and `""` — reaches `search_cover_by_title`
unchanged. `media_type` has no CHECK constraint in the schema, so that default
branch is load-bearing.

The regression that matters most is the book path: 1057 of 1057 real items take
it, and it must reach exactly the same function with exactly the same arguments
as before the seam existed.

Service-level throughout — no HTTP client, no FastAPI app (G14). The providers
are patched as attributes on `covers`, which is the G37-correct target because
`covers.py` imports `tmdb`/`igdb` at module level.
"""

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import covers


BOOK_FAMILY = ["book", "ebook", "audiobook", "kids_book", "comic"]

TMDB_CREDS = {"tmdb_api_key": "k"}
IGDB_CREDS = {"igdb_client_id": "cid", "igdb_client_secret": "secret"}


def _item(**over):
    base = {
        "media_type": "book",
        "title": "A Title",
        "authors": "An Author",
        "publish_year": None,
        "platform": None,
    }
    base.update(over)
    return base


def _row(**over):
    """A real `sqlite3.Row`, built with only the columns given.

    `sqlite3.Row` raises `IndexError` for a column that was not selected and
    has no `.get()` — the reason `_col` exists.
    """
    cols = _item(**over) if over.get("_full", True) else over
    cols.pop("_full", None)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    names = ", ".join(f'? AS "{c}"' for c in cols)
    return conn.execute(f"SELECT {names}", tuple(cols.values())).fetchone()


@pytest.fixture
def no_providers(monkeypatch):
    """Both provider modules stubbed so any call is visible and none is real.

    `image_url` is deliberately a **sync** `MagicMock`: it is a plain function
    in both clients, and an `AsyncMock` child would hand back an un-awaited
    coroutine that still passes a `len(result)` assertion — a candidate whose
    `url` is a coroutine object looks exactly like a working one until the
    template renders it.
    """
    tmdb = AsyncMock()
    igdb = AsyncMock()
    tmdb.image_url = MagicMock(side_effect=lambda p, size: f"https://image.tmdb.org/t/p/{size}{p}")
    igdb.image_url = MagicMock(
        side_effect=lambda i, size: f"https://images.igdb.com/igdb/image/upload/{size}/{i}.jpg"
    )
    monkeypatch.setattr(covers, "tmdb", tmdb)
    monkeypatch.setattr(covers, "igdb", igdb)
    return tmdb, igdb


@pytest.fixture
def book_search(monkeypatch):
    """`search_cover_by_title` patched by module attribute, as the picker's
    own tests do — the patch the seam must not detach."""
    stub = AsyncMock(return_value=[{"url": "u", "thumbnail": "t", "source": "Google Books"}])
    monkeypatch.setattr(covers, "search_cover_by_title", stub)
    return stub


class TestTheBookPathIsUnchanged:
    """The regression that would matter most."""

    @pytest.mark.parametrize("media_type", BOOK_FAMILY)
    async def test_every_book_family_type_reaches_search_cover_by_title(
        self, media_type, book_search, no_providers
    ):
        tmdb, igdb = no_providers

        result = await covers.search_covers(
            _item(media_type=media_type), "Dune", object(), creds={}
        )

        book_search.assert_awaited_once()
        args = book_search.await_args.args
        assert args[0] == "Dune"
        assert args[1] == "An Author"
        assert result == [{"url": "u", "thumbnail": "t", "source": "Google Books"}]
        assert tmdb.mock_calls == []
        assert igdb.mock_calls == []

    async def test_optional_google_key_reaches_book_cover_search(
        self, book_search, no_providers
    ):
        tmdb, igdb = no_providers
        await covers.search_covers(
            _item(media_type="book"), "Dune", object(),
            creds={"google_books_api_key": "google-key"},
        )

        assert book_search.await_args.kwargs["google_api_key"] == "google-key"
        assert tmdb.mock_calls == []
        assert igdb.mock_calls == []

    @pytest.mark.parametrize("media_type", ["vinyl_lp", None, ""])
    async def test_an_unknown_media_type_takes_the_book_branch(
        self, media_type, book_search, no_providers
    ):
        """G31 target: `media_type` is plain TEXT with no CHECK, so an unknown
        value must fall through rather than raise."""
        tmdb, igdb = no_providers

        await covers.search_covers(_item(media_type=media_type), "Q", object(), creds={})

        book_search.assert_awaited_once()
        assert tmdb.mock_calls == []
        assert igdb.mock_calls == []

    async def test_the_book_branch_resolves_the_patched_attribute_at_call_time(
        self, monkeypatch, no_providers
    ):
        """A local alias or a from-import would detach the eight existing
        `monkeypatch.setattr(covers, "search_cover_by_title", ...)` tests."""
        late = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_cover_by_title", late)

        await covers.search_covers(_item(), "Q", object(), creds={})

        late.assert_awaited_once()


class TestDvdReachesTmdb:
    async def test_a_dvd_reaches_tmdb_and_never_the_book_path(self, no_providers, book_search):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 603, "publish_year": 1999}]
        tmdb.search_posters.return_value = [{"file_path": "/a.jpg", "iso_639_1": "en"}]
        tmdb.image_url.side_effect = lambda p, size: f"https://image.tmdb.org/t/p/{size}{p}"

        result = await covers.search_covers(
            _item(media_type="dvd"), "The Matrix", object(), creds=TMDB_CREDS
        )

        tmdb.search_movies.assert_awaited_once()
        assert result == [{
            "url": "https://image.tmdb.org/t/p/w500/a.jpg",
            "thumbnail": "https://image.tmdb.org/t/p/w185/a.jpg",
            "source": "TMDb · EN",
        }]
        book_search.assert_not_awaited()

    async def test_each_poster_is_labelled_with_its_uppercased_language(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 1, "publish_year": None}]
        tmdb.search_posters.return_value = [
            {"file_path": "/a.jpg", "iso_639_1": "en"},
            {"file_path": "/b.jpg", "iso_639_1": "fr"},
        ]
        tmdb.image_url.side_effect = lambda p, size: f"{size}{p}"

        result = await covers.search_covers(_item(media_type="dvd"), "Q", object(), creds=TMDB_CREDS)

        assert [c["source"] for c in result] == ["TMDb · EN", "TMDb · FR"]

    @pytest.mark.parametrize("lang", [None, "", "   "])
    async def test_a_poster_with_no_language_is_labelled_plain_tmdb(self, lang, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 1, "publish_year": None}]
        tmdb.search_posters.return_value = [{"file_path": "/a.jpg", "iso_639_1": lang}]
        tmdb.image_url.side_effect = lambda p, size: f"{size}{p}"

        result = await covers.search_covers(_item(media_type="dvd"), "Q", object(), creds=TMDB_CREDS)

        assert result[0]["source"] == "TMDb"

    async def test_a_matching_publish_year_picks_that_hit_not_the_first(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [
            {"tmdb_id": 111, "publish_year": 1977},
            {"tmdb_id": 222, "publish_year": 1999},
        ]
        tmdb.search_posters.return_value = []
        tmdb.image_url.side_effect = lambda p, size: p

        await covers.search_covers(
            _item(media_type="dvd", publish_year=1999), "Q", object(), creds=TMDB_CREDS
        )

        assert tmdb.search_posters.await_args.args[0] == 222

    async def test_a_dvd_with_no_publish_year_resolves_via_the_first_hit(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [
            {"tmdb_id": 111, "publish_year": 1977},
            {"tmdb_id": 222, "publish_year": 1999},
        ]
        tmdb.search_posters.return_value = []
        tmdb.image_url.side_effect = lambda p, size: p

        await covers.search_covers(
            _item(media_type="dvd", publish_year=None), "Q", object(), creds=TMDB_CREDS
        )

        assert tmdb.search_posters.await_args.args[0] == 111

    async def test_a_publish_year_matching_nothing_falls_back_to_the_first_hit(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 111, "publish_year": 1977}]
        tmdb.search_posters.return_value = []
        tmdb.image_url.side_effect = lambda p, size: p

        await covers.search_covers(
            _item(media_type="dvd", publish_year=2020), "Q", object(), creds=TMDB_CREDS
        )

        assert tmdb.search_posters.await_args.args[0] == 111

    async def test_no_search_hits_yields_nothing_and_never_asks_for_posters(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = []

        result = await covers.search_covers(
            _item(media_type="dvd"), "Q", object(), creds=TMDB_CREDS
        )

        assert result == []
        tmdb.search_posters.assert_not_awaited()

    async def test_a_hit_with_no_tmdb_id_never_asks_for_posters(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": None, "publish_year": None}]

        result = await covers.search_covers(
            _item(media_type="dvd"), "Q", object(), creds=TMDB_CREDS
        )

        assert result == []
        tmdb.search_posters.assert_not_awaited()

    async def test_a_poster_with_no_file_path_is_skipped(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 1, "publish_year": None}]
        tmdb.search_posters.return_value = [
            {"file_path": None, "iso_639_1": "en"},
            {"file_path": "/b.jpg", "iso_639_1": "en"},
        ]
        tmdb.image_url.side_effect = lambda p, size: f"{size}{p}"

        result = await covers.search_covers(_item(media_type="dvd"), "Q", object(), creds=TMDB_CREDS)

        assert len(result) == 1

    async def test_the_tmdb_gallery_caps_at_twelve(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 1, "publish_year": None}]
        tmdb.search_posters.return_value = [
            {"file_path": f"/{i}.jpg", "iso_639_1": "en"} for i in range(30)
        ]
        tmdb.image_url.side_effect = lambda p, size: f"{size}{p}"

        result = await covers.search_covers(_item(media_type="dvd"), "Q", object(), creds=TMDB_CREDS)

        assert len(result) == covers.MAX_CANDIDATES == 12


class TestVideoGameReachesIgdb:
    async def test_a_game_reaches_igdb_and_never_the_book_path(self, no_providers, book_search):
        _, igdb = no_providers
        igdb.search_game_art.return_value = [
            {"title": "Portal", "cover_image_id": "c1", "artwork_image_ids": ["a1"]}
        ]
        igdb.image_url.side_effect = lambda i, size: f"{size}/{i}"

        result = await covers.search_covers(
            _item(media_type="video_game"), "Portal", object(), creds=IGDB_CREDS
        )

        igdb.search_game_art.assert_awaited_once()
        assert len(result) == 2
        book_search.assert_not_awaited()

    async def test_cover_and_artwork_are_separate_candidates_cover_first(self, no_providers):
        _, igdb = no_providers
        igdb.search_game_art.return_value = [
            {"title": "Portal", "cover_image_id": "c1", "artwork_image_ids": ["a1", "a2"]}
        ]
        igdb.image_url.side_effect = lambda i, size: f"{size}/{i}"

        result = await covers.search_covers(
            _item(media_type="video_game"), "Q", object(), creds=IGDB_CREDS
        )

        assert [c["source"] for c in result] == [
            "IGDB · Portal · cover",
            "IGDB · Portal · art",
            "IGDB · Portal · art",
        ]
        assert result[0]["url"] == "t_cover_big/c1"
        assert result[0]["thumbnail"] == "t_cover_small/c1"
        assert result[1]["url"] == "t_720p/a1"
        assert result[1]["thumbnail"] == "t_screenshot_med/a1"

    async def test_a_game_with_no_cover_still_yields_its_artwork(self, no_providers):
        _, igdb = no_providers
        igdb.search_game_art.return_value = [
            {"title": "Portal", "cover_image_id": None, "artwork_image_ids": ["a1"]}
        ]
        igdb.image_url.side_effect = lambda i, size: f"{size}/{i}"

        result = await covers.search_covers(
            _item(media_type="video_game"), "Q", object(), creds=IGDB_CREDS
        )

        assert [c["source"] for c in result] == ["IGDB · Portal · art"]

    async def test_a_long_game_title_is_truncated_in_the_label(self, no_providers):
        _, igdb = no_providers
        igdb.search_game_art.return_value = [
            {"title": "Super Mario Odyssey Deluxe Edition", "cover_image_id": "c1",
             "artwork_image_ids": []}
        ]
        igdb.image_url.side_effect = lambda i, size: i

        result = await covers.search_covers(
            _item(media_type="video_game"), "Q", object(), creds=IGDB_CREDS
        )

        name = result[0]["source"].removeprefix("IGDB · ").removesuffix(" · cover")
        assert len(name) == 24
        assert name.endswith("…")

    async def test_the_platform_column_is_passed_through_to_igdb(self, no_providers):
        _, igdb = no_providers
        igdb.search_game_art.return_value = []

        await covers.search_covers(
            _item(media_type="video_game", platform="snes"), "Q", object(), creds=IGDB_CREDS
        )

        assert igdb.search_game_art.await_args.kwargs["platform"] == "snes"

    async def test_the_igdb_gallery_caps_at_twelve_across_all_games(self, no_providers):
        _, igdb = no_providers
        igdb.search_game_art.return_value = [
            {"title": f"G{i}", "cover_image_id": f"c{i}", "artwork_image_ids": [f"a{i}", f"b{i}"]}
            for i in range(5)
        ]
        igdb.image_url.side_effect = lambda i, size: i

        result = await covers.search_covers(
            _item(media_type="video_game"), "Q", object(), creds=IGDB_CREDS
        )

        assert len(result) == covers.MAX_CANDIDATES == 12


class TestNoProviderCanRaise:
    """HTTP 500, a timeout, a missing credential and an empty result set are
    all the same `[]` to the picker — and none of them is an exception."""

    @pytest.mark.parametrize("media_type,creds", [("dvd", TMDB_CREDS), ("video_game", IGDB_CREDS)])
    async def test_an_upstream_failure_yields_an_empty_list(
        self, media_type, creds, no_providers
    ):
        tmdb, igdb = no_providers
        tmdb.search_movies.side_effect = RuntimeError("HTTP 500")
        igdb.search_game_art.side_effect = RuntimeError("HTTP 500")

        result = await covers.search_covers(_item(media_type=media_type), "Q", object(), creds=creds)

        assert result == []

    @pytest.mark.parametrize("media_type,creds", [("dvd", TMDB_CREDS), ("video_game", IGDB_CREDS)])
    async def test_a_raised_timeout_yields_an_empty_list(self, media_type, creds, no_providers):
        tmdb, igdb = no_providers
        tmdb.search_movies.side_effect = TimeoutError("slow")
        igdb.search_game_art.side_effect = TimeoutError("slow")

        result = await covers.search_covers(_item(media_type=media_type), "Q", object(), creds=creds)

        assert result == []

    @pytest.mark.parametrize("media_type,creds", [("dvd", TMDB_CREDS), ("video_game", IGDB_CREDS)])
    async def test_an_empty_result_set_yields_an_empty_list(self, media_type, creds, no_providers):
        tmdb, igdb = no_providers
        tmdb.search_movies.return_value = []
        igdb.search_game_art.return_value = []

        result = await covers.search_covers(_item(media_type=media_type), "Q", object(), creds=creds)

        assert result == []

    @pytest.mark.parametrize("media_type", ["dvd", "video_game"])
    @pytest.mark.parametrize("creds", [{}, {"tmdb_api_key": "", "igdb_client_id": "",
                                          "igdb_client_secret": ""}])
    async def test_an_absent_credential_yields_nothing_without_any_outbound_call(
        self, media_type, creds, no_providers
    ):
        tmdb, igdb = no_providers

        result = await covers.search_covers(_item(media_type=media_type), "Q", object(), creds=creds)

        assert result == []
        tmdb.search_movies.assert_not_awaited()
        tmdb.search_posters.assert_not_awaited()
        igdb.search_game_art.assert_not_awaited()

    async def test_a_whitespace_only_credential_counts_as_missing(self, no_providers):
        tmdb, _ = no_providers

        result = await covers.search_covers(
            _item(media_type="dvd"), "Q", object(), creds={"tmdb_api_key": "   "}
        )

        assert result == []
        tmdb.search_movies.assert_not_awaited()

    async def test_one_igdb_operand_alone_is_not_enough(self, no_providers):
        """G49: a compound credential needs every operand, not the first one."""
        _, igdb = no_providers

        for creds in ({"igdb_client_id": "cid"}, {"igdb_client_secret": "secret"}):
            result = await covers.search_covers(
                _item(media_type="video_game"), "Q", object(), creds=creds
            )
            assert result == []

        igdb.search_game_art.assert_not_awaited()


class TestTheRowShapesItActuallyGets:
    async def test_a_sqlite_row_missing_platform_does_not_raise(self, no_providers):
        """`sqlite3.Row` raises `IndexError` for an unselected column and has
        no `.get()` — a bare subscript here would be a 500."""
        _, igdb = no_providers
        igdb.search_game_art.return_value = []
        row = _row(_full=False, media_type="video_game", title="T", authors=None)

        result = await covers.search_covers(row, "Q", object(), creds=IGDB_CREDS)

        assert result == []
        assert igdb.search_game_art.await_args.kwargs["platform"] is None

    async def test_a_sqlite_row_missing_publish_year_does_not_raise(self, no_providers):
        tmdb, _ = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 7, "publish_year": 1999}]
        tmdb.search_posters.return_value = []
        row = _row(_full=False, media_type="dvd", title="T", authors=None)

        result = await covers.search_covers(row, "Q", object(), creds=TMDB_CREDS)

        assert result == []
        assert tmdb.search_posters.await_args.args[0] == 7

    async def test_a_sqlite_row_on_the_book_path_passes_its_authors_through(
        self, book_search, no_providers
    ):
        row = _row(_full=False, media_type="book", title="T", authors="Le Guin, Ursula")

        await covers.search_covers(row, "Q", object(), creds={})

        assert book_search.await_args.args[1] == "Le Guin, Ursula"


class TestTheCandidateContract:
    @pytest.mark.parametrize("media_type,creds", [("dvd", TMDB_CREDS), ("video_game", IGDB_CREDS)])
    async def test_every_candidate_has_exactly_url_thumbnail_and_source(
        self, media_type, creds, no_providers
    ):
        tmdb, igdb = no_providers
        tmdb.search_movies.return_value = [{"tmdb_id": 1, "publish_year": None}]
        tmdb.search_posters.return_value = [{"file_path": "/a.jpg", "iso_639_1": "en"}]
        tmdb.image_url.side_effect = lambda p, size: p
        igdb.search_game_art.return_value = [
            {"title": "G", "cover_image_id": "c", "artwork_image_ids": ["a"]}
        ]
        igdb.image_url.side_effect = lambda i, size: i

        result = await covers.search_covers(_item(media_type=media_type), "Q", object(), creds=creds)

        assert result
        for candidate in result:
            assert set(candidate) == {"url", "thumbnail", "source"}


class TestRequiredCredentials:
    """The one table the router's "which credential is missing?" gate reads,
    so the gate and the dispatch cannot disagree (G49)."""

    def test_a_dvd_needs_the_tmdb_key(self):
        assert covers.required_credentials("dvd") == ("tmdb_api_key",)

    def test_a_video_game_needs_both_igdb_fields(self):
        assert covers.required_credentials("video_game") == (
            "igdb_client_id", "igdb_client_secret",
        )

    @pytest.mark.parametrize("media_type", BOOK_FAMILY + ["vinyl_lp", None, ""])
    def test_everything_on_the_book_path_needs_no_credential(self, media_type):
        assert covers.required_credentials(media_type) == ()

    def test_every_provider_in_the_dispatch_table_has_credential_keys(self):
        """A provider added to one table and not the other would dispatch to a
        service the gate believes needs nothing."""
        assert set(covers.MEDIA_TYPE_PROVIDERS.values()) <= set(covers.CREDENTIAL_KEYS)
