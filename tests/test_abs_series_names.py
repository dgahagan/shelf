"""Audiobookshelf reports an item's series as "<name> #<sequence>"; Shelf stores
the name and the position separately so a series groups as one."""
import asyncio

import httpx
import pytest
import respx

from app.services.audiobookshelf import _split_series, sync
from tests.conftest import _insert_item

ABS = "http://abs.example:13378"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Discworld #3", ("Discworld", 3.0)),
        ("郭论 #6", ("郭论", 6.0)),
        ("The Expanse #1.5", ("The Expanse", 1.5)),
        # Several series are comma-joined: the first one and its sequence.
        ("Discworld #3, Rincewind #1", ("Discworld", 3.0)),
        ("Anno 2049 #12", ("Anno 2049", 12.0)),
        # No numeric sequence: the name is kept exactly as it is.
        ("Dune", ("Dune", None)),
        ("Mistborn #Era 2", ("Mistborn #Era 2", None)),
        ("#3", ("#3", None)),
        ("  ", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_series(raw, expected):
    assert _split_series(raw) == expected


def _mock_library(series_name):
    respx.get(f"{ABS}/api/libraries").mock(return_value=httpx.Response(200, json={"libraries": [
        {"id": "lib_audio", "name": "Audiobooks", "mediaType": "book"},
    ]}))
    respx.get(f"{ABS}/api/libraries/lib_audio/items").mock(return_value=httpx.Response(200, json={"results": [
        {
            "id": "li_1",
            "media": {
                "metadata": {"title": "Guo Lun 6", "seriesName": series_name},
                "numAudioFiles": 1,
                "duration": 3600,
            },
        },
    ]}))
    respx.get(f"{ABS}/api/items/li_1/cover").mock(return_value=httpx.Response(404))


def _series(db):
    row = db.execute("SELECT series_name, series_position FROM items WHERE abs_id = 'li_1'").fetchone()
    return row["series_name"], row["series_position"]


@respx.mock
def test_sync_stores_series_name_and_position(db):
    _mock_library("郭论 #6")

    stats = asyncio.run(sync(ABS, "token"))

    assert stats["added"] == 1
    assert _series(db) == ("郭论", 6.0)

    # The same data again is a no-op, not a rewrite on every sync.
    again = asyncio.run(sync(ABS, "token"))
    assert again["unchanged"] == 1
    assert again["updated"] == 0


@respx.mock
def test_sync_repairs_a_series_stored_with_its_sequence(db):
    # How a series synced before this change was stored.
    _insert_item(db, title="Guo Lun 6", isbn=None, media_type="audiobook",
                 abs_id="li_1", abs_library_id="lib_audio", series_name="郭论 #6")
    db.execute("COMMIT")
    _mock_library("郭论 #6")

    stats = asyncio.run(sync(ABS, "token"))

    assert stats["updated"] == 1
    assert _series(db) == ("郭论", 6.0)


@respx.mock
def test_sync_keeps_a_series_without_sequence(db):
    _mock_library("Dune Chronicles")

    asyncio.run(sync(ABS, "token"))

    assert _series(db) == ("Dune Chronicles", None)
