"""T1 — cover-search query override and cover-select's non-destructive failure path."""
import re
from unittest.mock import AsyncMock

from tests.conftest import _insert_item


class TestCoverSearchQuery:
    def test_query_overrides_stored_title(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900003001")
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_cover_by_title", search)

        resp = editor_client.get(
            f"/api/items/{item_id}/cover-search", params={"query": "Custom Query"}
        )

        assert resp.status_code == 200
        args, _ = search.await_args
        assert args[0] == "Custom Query"

    def test_blank_query_falls_back_to_stored_title(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900003002")
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_cover_by_title", search)

        resp = editor_client.get(
            f"/api/items/{item_id}/cover-search", params={"query": "   "}
        )

        assert resp.status_code == 200
        args, _ = search.await_args
        assert args[0] == "Stored Title"


class TestCoverSelectFailure:
    def test_failed_select_rerenders_gallery_and_keeps_error_toast(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900003003")
        db.commit()

        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        monkeypatch.setattr(
            covers, "search_cover_by_title",
            AsyncMock(return_value=[
                {"url": "https://example.test/a.jpg", "thumbnail": "https://example.test/a-thumb.jpg", "source": "Test"},
            ]),
        )

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/bad.jpg"},
        )

        assert resp.status_code == 200
        assert "Select a cover" in resp.text
        assert "example.test/a.jpg" in resp.text
        assert "HX-Redirect" not in resp.headers
        trigger = resp.headers.get("HX-Trigger", "")
        assert "Failed to download cover" in trigger
        assert "error" in trigger

    def test_successful_select_still_sends_hx_redirect(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900003004")
        db.commit()

        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value="covers/x.jpg"))

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/good.jpg"},
        )

        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect") == f"/item/{item_id}"

    def test_failed_select_with_custom_query_reruns_search_and_seeds_box(self, editor_client, db, monkeypatch):
        from app.main import app as fastapi_app
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900003005")
        db.commit()

        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_cover_by_title", search)

        # No query box exists yet (a later task adds it) so the only way to
        # observe that the box would be seeded is the template context the
        # route hands the fragment — spy on the render call to capture it.
        templates = fastapi_app.state.templates
        original_render = templates.TemplateResponse
        captured = {}

        def spy_render(request, name, context=None, *args, **kwargs):
            captured["context"] = context
            return original_render(request, name, context, *args, **kwargs)

        monkeypatch.setattr(templates, "TemplateResponse", spy_render)

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/bad.jpg", "query": "Custom Retry Query"},
        )

        assert resp.status_code == 200
        args, _ = search.await_args
        assert args[0] == "Custom Retry Query"
        assert captured["context"]["query"] == "Custom Retry Query"

    def test_failed_select_candidate_buttons_carry_hx_target(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900003006")
        db.commit()

        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        monkeypatch.setattr(
            covers, "search_cover_by_title",
            AsyncMock(return_value=[
                {"url": "https://example.test/a.jpg", "thumbnail": "https://example.test/a-thumb.jpg", "source": "Test A"},
                {"url": "https://example.test/b.jpg", "thumbnail": "https://example.test/b-thumb.jpg", "source": "Test B"},
            ]),
        )

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/bad.jpg"},
        )

        assert resp.status_code == 200
        # Count cover-select posts specifically. The fragment also carries an
        # upload form and (when a cover exists) a Remove button, both of which
        # are `hx-post="/api/items/…"` and both of which must NOT have a target.
        select_count = resp.text.count(f'hx-post="/api/items/{item_id}/cover-select"')
        target_count = resp.text.count('hx-target="#cover-candidates" hx-swap="innerHTML"')
        assert select_count == 2
        # One target per tile, plus the query box's own.
        assert target_count == select_count + 1


class TestCoverUpload:
    """POST /api/items/{id}/cover-upload — the negative cases matter most:
    `save_uploaded_cover` writes straight to `{item_id}.jpg`, so a validation
    miss overwrites a good cover with junk."""

    @staticmethod
    def _jpeg(size=512):
        return b"\xff\xd8\xff" + b"\x00" * (size - 3)

    @staticmethod
    def _post(client, item_id, blob, filename="cover.jpg", mime="image/jpeg"):
        import io
        return client.post(
            f"/api/items/{item_id}/cover-upload",
            files={"cover": (filename, io.BytesIO(blob), mime)},
        )

    def test_valid_jpeg_sets_cover_path(self, editor_client, db):
        import app.config

        item_id = _insert_item(db, title="Upload Me", isbn="9780900004001")
        db.commit()

        resp = self._post(editor_client, item_id, self._jpeg())

        assert resp.status_code == 200
        assert resp.text == ""
        assert resp.headers.get("HX-Redirect") == f"/item/{item_id}"
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] == f"covers/{item_id}.jpg"
        assert (app.config.COVERS_DIR / f"{item_id}.jpg").exists()

    def test_valid_upload_stamps_updated_at(self, editor_client, db):
        item_id = _insert_item(db, title="Stamp Me", isbn="9780900004005")
        db.execute("UPDATE items SET updated_at = '2000-01-01 00:00:00' WHERE id = ?", (item_id,))
        db.commit()

        assert self._post(editor_client, item_id, self._jpeg()).status_code == 200

        row = db.execute("SELECT updated_at FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["updated_at"] != "2000-01-01 00:00:00"

    def test_undersize_file_is_refused_and_writes_nothing(self, editor_client, db):
        import app.config

        item_id = _insert_item(db, title="Too Small", isbn="9780900004002")
        db.commit()

        resp = self._post(editor_client, item_id, self._jpeg(50))

        assert resp.status_code == 200
        # An empty body is the contract: the form is hx-swap="none" with no
        # hx-target, so any markup here would blank the picker on a rejection.
        assert resp.text == ""
        assert "HX-Redirect" not in resp.headers
        assert "error" in resp.headers.get("HX-Trigger", "")
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] is None
        assert not (app.config.COVERS_DIR / f"{item_id}.jpg").exists()

    def test_non_image_is_refused_and_writes_nothing(self, editor_client, db):
        import app.config

        item_id = _insert_item(db, title="Not An Image", isbn="9780900004003")
        db.commit()

        resp = self._post(
            editor_client, item_id, b"%PDF-1.7" + b"\x00" * 500,
            filename="cover.pdf", mime="application/pdf",
        )

        assert resp.status_code == 200
        assert resp.text == ""
        assert "HX-Redirect" not in resp.headers
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] is None
        assert not (app.config.COVERS_DIR / f"{item_id}.jpg").exists()

    def test_oversize_file_is_refused_and_writes_nothing(self, editor_client, db):
        import app.config
        from app.services import covers

        item_id = _insert_item(db, title="Too Big", isbn="9780900004004")
        db.commit()

        blob = b"\xff\xd8\xff" + b"\x00" * (covers.MAX_COVER_SIZE - 2)
        assert len(blob) == covers.MAX_COVER_SIZE + 1

        resp = self._post(editor_client, item_id, blob)

        assert resp.status_code == 200
        assert resp.text == ""
        assert "HX-Redirect" not in resp.headers
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] is None
        assert not (app.config.COVERS_DIR / f"{item_id}.jpg").exists()

    def test_rejected_upload_leaves_an_existing_cover_alone(self, editor_client, db):
        import app.config

        item_id = _insert_item(db, title="Keep My Cover", isbn="9780900004006")
        db.execute(
            "UPDATE items SET cover_path = ? WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        db.commit()
        good = app.config.COVERS_DIR / f"{item_id}.jpg"
        good.write_bytes(self._jpeg())

        resp = self._post(editor_client, item_id, b"%PDF-1.7" + b"\x00" * 500)

        assert resp.status_code == 200
        row = db.execute("SELECT cover_path FROM items WHERE id = ?", (item_id,)).fetchone()
        assert row["cover_path"] == f"covers/{item_id}.jpg"
        assert good.read_bytes() == self._jpeg()

    def test_unknown_item_is_404(self, editor_client, db):
        assert self._post(editor_client, 999999, self._jpeg()).status_code == 404

    def test_viewer_forbidden(self, viewer_client, db):
        item_id = _insert_item(db, title="Viewer Upload", isbn="9780900004007")
        db.commit()
        resp = self._post(viewer_client, item_id, self._jpeg())
        assert resp.status_code in (401, 403)


class TestCoverRemove:
    def test_remove_clears_the_column(self, editor_client, db):
        item_id = _insert_item(db, title="Remove Me", isbn="9780900005001")
        db.execute(
            "UPDATE items SET cover_path = ?, updated_at = '2000-01-01 00:00:00' WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        db.commit()

        resp = editor_client.post(f"/api/items/{item_id}/cover-remove")

        assert resp.status_code == 200
        assert resp.text == ""
        assert resp.headers.get("HX-Redirect") == f"/item/{item_id}"
        row = db.execute(
            "SELECT cover_path, updated_at FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        assert row["cover_path"] is None
        assert row["updated_at"] != "2000-01-01 00:00:00"

    def test_remove_leaves_the_file_on_disk(self, editor_client, db):
        import app.config

        item_id = _insert_item(db, title="Keep The File", isbn="9780900005002")
        db.execute(
            "UPDATE items SET cover_path = ? WHERE id = ?",
            (f"covers/{item_id}.jpg", item_id),
        )
        db.commit()
        on_disk = app.config.COVERS_DIR / f"{item_id}.jpg"
        on_disk.write_bytes(b"\xff\xd8\xff" + b"\x00" * 509)

        assert editor_client.post(f"/api/items/{item_id}/cover-remove").status_code == 200

        assert on_disk.exists()

    def test_unknown_item_is_404(self, editor_client, db):
        assert editor_client.post("/api/items/999999/cover-remove").status_code == 404

    def test_viewer_forbidden(self, viewer_client, db):
        item_id = _insert_item(db, title="Viewer Remove", isbn="9780900005003")
        db.commit()
        resp = viewer_client.post(f"/api/items/{item_id}/cover-remove")
        assert resp.status_code in (401, 403)


class TestPickerReachability:
    """T5 — pins for the picker's reachability, the role guard, and the
    failure-recovery contract. Each of these is a one-attribute regression
    (drop a guard, move a block, drop an attribute) that a route test cannot
    see because it only inspects status codes and DB state, not markup."""

    def test_picker_controls_present_with_a_cover(self, editor_client, db):
        # This is the regression that defines the whole plan: before T3, the
        # control group and #cover-candidates lived inside the no-cover arm,
        # so an item that already had a cover could never re-pick one.
        item_id = _insert_item(
            db, title="Has Cover", isbn="9780900006001", cover_path="covers/x.jpg",
        )
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert 'data-testid="cover-controls"' in html
        assert 'id="cover-candidates"' in html

    def test_picker_controls_present_without_a_cover(self, editor_client, db):
        item_id = _insert_item(db, title="No Cover", isbn="9780900006002")
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert 'data-testid="cover-controls"' in html
        assert 'id="cover-candidates"' in html
        assert "Retry ISBN" in html

    def test_retry_isbn_absent_once_a_cover_exists(self, editor_client, db):
        item_id = _insert_item(
            db, title="Has Cover Too", isbn="9780900006003", cover_path="covers/y.jpg",
        )
        db.commit()

        html = editor_client.get(f"/item/{item_id}").text

        assert "Retry ISBN" not in html

    def test_viewer_gets_no_picker_and_no_cover_mutation_endpoints(self, viewer_client, db):
        item_id = _insert_item(
            db, title="Viewer Sees Nothing", isbn="9780900006004", cover_path="covers/z.jpg",
        )
        db.commit()

        html = viewer_client.get(f"/item/{item_id}").text

        assert 'data-testid="cover-controls"' not in html
        assert "cover-search" not in html
        assert "retry-cover" not in html
        assert "cover-upload" not in html
        assert "cover-remove" not in html

    def test_fragment_shows_current_cover_tile_when_item_has_one(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(
            db, title="Current Cover Item", isbn="9780900006005", cover_path="covers/existing.jpg",
        )
        db.commit()

        monkeypatch.setattr(covers, "search_cover_by_title", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert 'data-testid="current-cover"' in resp.text

    def test_fragment_wiring_targets_and_swap_modes(self, editor_client, db, monkeypatch):
        """The wiring no route test can see: cover-select buttons target the
        candidate grid; the upload form and Remove button do not — they are
        hx-swap="none" precisely so a rejection doesn't blank the picker."""
        from app.services import covers

        item_id = _insert_item(
            db, title="Wiring Item", isbn="9780900006006", cover_path="covers/existing2.jpg",
        )
        db.commit()

        monkeypatch.setattr(
            covers, "search_cover_by_title",
            AsyncMock(return_value=[
                {"url": "https://example.test/a.jpg", "thumbnail": "https://example.test/a-thumb.jpg", "source": "Test A"},
                {"url": "https://example.test/b.jpg", "thumbnail": "https://example.test/b-thumb.jpg", "source": "Test B"},
            ]),
        )

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")
        assert resp.status_code == 200
        body = resp.text

        select_tags = re.findall(
            r'<button[^>]*hx-post="/api/items/%d/cover-select"[^>]*>' % item_id, body,
        )
        assert len(select_tags) == 2
        for tag in select_tags:
            assert 'hx-target="#cover-candidates"' in tag

        upload_tags = re.findall(r'<form[^>]*data-testid="cover-upload"[^>]*>', body)
        assert len(upload_tags) == 1
        assert 'hx-swap="none"' in upload_tags[0]
        assert "hx-target" not in upload_tags[0]

        remove_tags = re.findall(r'<button[^>]*data-testid="cover-remove"[^>]*>', body)
        assert len(remove_tags) == 1
        assert 'hx-swap="none"' in remove_tags[0]
        assert "hx-target" not in remove_tags[0]

    def test_failed_select_seeds_the_query_box_in_rendered_markup(self, editor_client, db, monkeypatch):
        """T1's TestCoverSelectFailure.test_failed_select_with_custom_query_reruns_search_and_seeds_box
        pinned this via a template-context spy, because the query box didn't
        exist in rendered markup yet. Now that T4 shipped the input, pin the
        markup-level half: the input's own `value` attribute."""
        from app.services import covers

        item_id = _insert_item(db, title="Stored Title", isbn="9780900006007")
        db.commit()

        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        monkeypatch.setattr(covers, "search_cover_by_title", AsyncMock(return_value=[]))

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/bad.jpg", "query": "Custom Retry Query"},
        )

        assert resp.status_code == 200
        input_tags = re.findall(
            r'<input[^>]*id="cover-query-%d"[^>]*>' % item_id, resp.text,
        )
        assert len(input_tags) == 1
        assert 'value="Custom Retry Query"' in input_tags[0]


class TestTheRouteKnowsWhatTheItemIs:
    """T4 — the picker routes hand `search_covers` the item and its provider
    credentials. Both call sites: `cover_search`, and `cover_select`'s failure
    re-render, which is the one the design flags as easy to miss.

    Every test here commits before the request (G48) — `get_db()` commits on
    context-manager exit and the `db` fixture holds its block open until
    teardown, so an uncommitted seed makes the assertion pass vacuously.
    """

    def test_a_dvd_search_passes_the_items_media_type(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="The Matrix", isbn="9780900007001", media_type="dvd")
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        item = search.await_args.args[0]
        assert item["media_type"] == "dvd"

    def test_a_game_search_passes_platform_and_publish_year_through(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(
            db, title="Super Metroid", isbn="9780900007002",
            media_type="video_game", platform="snes", publish_year=1994,
        )
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        item = search.await_args.args[0]
        assert item["platform"] == "snes"
        assert item["publish_year"] == 1994

    def test_a_failed_select_on_a_dvd_re_renders_through_the_dvd_path(
        self, editor_client, db, monkeypatch
    ):
        """The second call site. Widening one SELECT and not the other would
        leave this path blind to media type and silently show book results."""
        from app.services import covers

        item_id = _insert_item(db, title="Alien", isbn="9780900007003", media_type="dvd")
        db.commit()

        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/bad.jpg"},
        )

        assert resp.status_code == 200
        item = search.await_args.args[0]
        assert item["media_type"] == "dvd"

    def test_a_dvd_gets_the_tmdb_key_in_creds(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Heat", isbn="9780900007004", media_type="dvd")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("tmdb_api_key", "a-key"))
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        editor_client.get(f"/api/items/{item_id}/cover-search")

        assert search.await_args.kwargs["creds"] == {"tmdb_api_key": "a-key"}

    def test_a_video_game_gets_both_igdb_keys_in_creds(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Doom", isbn="9780900007005", media_type="video_game")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_id", "cid"))
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_secret", "secret"))
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        editor_client.get(f"/api/items/{item_id}/cover-search")

        assert search.await_args.kwargs["creds"] == {
            "igdb_client_id": "cid", "igdb_client_secret": "secret",
        }

    def test_a_book_gets_no_credentials_at_all(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Dune", isbn="9780900007006")
        db.commit()

        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        editor_client.get(f"/api/items/{item_id}/cover-search")

        assert search.await_args.kwargs["creds"] == {}

    def test_book_cover_search_receives_env_google_key(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Dune", isbn="9780900007007")
        db.commit()
        monkeypatch.setenv("GOOGLE_BOOKS_API_KEY", "env-google-key")
        search = AsyncMock(return_value=[])
        monkeypatch.setattr(covers, "search_covers", search)

        editor_client.get(f"/api/items/{item_id}/cover-search")

        assert search.await_args.kwargs["creds"] == {
            "google_books_api_key": "env-google-key"
        }


class TestTheUnconfiguredProviderNote:
    """T4 — "not configured" is not "not found". The note is `None` unless a
    required credential is actually missing."""

    def test_a_dvd_with_no_tmdb_key_is_told_which_credential_is_missing(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Tron", isbn="9780900008001", media_type="dvd")
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert resp.context["search_note"] is not None
        assert "TMDb API key" in resp.context["search_note"]

    def test_an_env_only_tmdb_key_is_not_reported_as_unconfigured(
        self, editor_client, db, monkeypatch
    ):
        """G15: `TMDB_API_KEY` is in SECRET_ENV_VARS and the bulk settings
        accessor returns only keys that have a row, so an env-only install
        would otherwise be told to add a key it has already set."""
        from app.services import covers

        item_id = _insert_item(db, title="Tron", isbn="9780900008002", media_type="dvd")
        db.commit()
        monkeypatch.setenv("TMDB_API_KEY", "from-the-environment")
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.context["search_note"] is None
        assert "TMDb API key" not in resp.text

    def test_only_the_igdb_client_id_is_still_unconfigured(
        self, editor_client, db, monkeypatch
    ):
        """G49, operand one. A presence flag for one member of a compound
        credential does not satisfy the other."""
        from app.services import covers

        item_id = _insert_item(db, title="Doom", isbn="9780900008003", media_type="video_game")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_id", "cid"))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        note = resp.context["search_note"]
        assert note is not None
        assert "Client ID" in note and "Client Secret" in note

    def test_only_the_igdb_client_secret_is_still_unconfigured(
        self, editor_client, db, monkeypatch
    ):
        """G49, operand two — the half a one-sided gate silently passes."""
        from app.services import covers

        item_id = _insert_item(db, title="Doom", isbn="9780900008004", media_type="video_game")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_secret", "secret"))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        note = resp.context["search_note"]
        assert note is not None
        assert "Client ID" in note and "Client Secret" in note

    def test_both_igdb_fields_set_leaves_no_note(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Doom", isbn="9780900008005", media_type="video_game")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_id", "cid"))
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_secret", "secret"))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.context["search_note"] is None

    def test_a_whitespace_only_credential_counts_as_missing(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Tron", isbn="9780900008006", media_type="dvd")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("tmdb_api_key", "   "))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.context["search_note"] is not None

    def test_a_configured_provider_that_found_nothing_gets_no_note(
        self, editor_client, db, monkeypatch
    ):
        """Then the generic "No covers found for this title." line still
        applies — this is the distinction the whole note exists to draw."""
        from app.services import covers

        item_id = _insert_item(db, title="Tron", isbn="9780900008007", media_type="dvd")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("tmdb_api_key", "a-key"))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.context["search_note"] is None

    def test_a_book_never_gets_a_note(self, editor_client, db, monkeypatch):
        from app.services import covers

        item_id = _insert_item(db, title="Dune", isbn="9780900008008")
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.context["search_note"] is None

    def test_the_failure_re_render_also_carries_the_note(
        self, editor_client, db, monkeypatch
    ):
        """Both call sites compute it, not just the search one."""
        from app.services import covers

        item_id = _insert_item(db, title="Tron", isbn="9780900008009", media_type="dvd")
        db.commit()
        monkeypatch.setattr(covers, "_download_to_item", AsyncMock(return_value=None))
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.post(
            f"/api/items/{item_id}/cover-select",
            data={"url": "https://example.test/bad.jpg"},
        )

        assert resp.status_code == 200
        assert "TMDb API key" in resp.context["search_note"]


class TestTheSearchNoteRendersInTheFragment:
    """T5 — the template renders `search_note` in place of the generic empty
    line when the route supplied one, and widens the tile caption so the
    longer provider labels wrap instead of overflowing. Asserts on `resp.text`
    (the rendered markup), which is the half T5 adds — T4's tests above only
    ever checked `resp.context["search_note"]`.

    Every test here commits before the request (G48).
    """

    def test_a_dvd_with_no_tmdb_key_renders_the_tmdb_message_and_not_the_generic_one(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Tron", isbn="9780900009001", media_type="dvd")
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert "TMDb API key" in resp.text
        assert "No covers found for this title." not in resp.text

    def test_a_video_game_missing_one_igdb_field_renders_a_message_naming_both_fields(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(
            db, title="Doom", isbn="9780900009002", media_type="video_game"
        )
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("igdb_client_id", "cid"))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert "Client ID" in resp.text
        assert "Client Secret" in resp.text

    def test_a_configured_dvd_with_no_candidates_renders_the_generic_line_not_the_tmdb_message(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Heat", isbn="9780900009003", media_type="dvd")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("tmdb_api_key", "a-key"))
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert "No covers found for this title." in resp.text
        assert "TMDb API key" not in resp.text

    def test_a_book_renders_the_generic_line_exactly_as_today(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Dune", isbn="9780900009004")
        db.commit()
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert "No covers found for this title." in resp.text

    def test_the_caption_still_renders_the_candidate_source_text(
        self, editor_client, db, monkeypatch
    ):
        from app.services import covers

        item_id = _insert_item(db, title="Alien", isbn="9780900009005", media_type="dvd")
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("tmdb_api_key", "a-key"))
        db.commit()
        candidate = {
            "url": "https://example.test/cover.jpg",
            "thumbnail": "https://example.test/cover-thumb.jpg",
            "source": "TMDb · EN",
        }
        monkeypatch.setattr(covers, "search_covers", AsyncMock(return_value=[candidate]))

        resp = editor_client.get(f"/api/items/{item_id}/cover-search")

        assert resp.status_code == 200
        assert "TMDb · EN" in resp.text
