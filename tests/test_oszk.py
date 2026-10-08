"""OSZK NEKTÁR parser and Z39.50 client behavior, without live calls."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import browse_counts, oszk, provider_result, scan_outcome

FIXTURE = Path(__file__).parent / "fixtures" / "oszk_9786155420818.mrc"


class FakeProcess:
    def __init__(self, record: bytes | None, *, stdout=b"Search was a success.\nNumber of hits: 1", stderr=b""):
        self.record = record
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0
        self.killed = False
        self.marc_path = None
        self.input = None

    async def communicate(self, _input=None):
        self.input = _input
        if self.killed:
            return b"", b""
        if self.record is not None:
            Path(self.marc_path).write_bytes(self.record)
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    acquire = AsyncMock()
    monkeypatch.setattr(oszk.outbound, "acquire", acquire)
    return acquire


def stub_process(monkeypatch, process):
    async def create(*args, **kwargs):
        assert args[0] == "yaz-client"
        assert args[1] == "-m"
        assert args[3] == "tagetes2.oszk.hu:1616/ANY"
        process.marc_path = args[2]
        return process

    monkeypatch.setattr(oszk.asyncio, "create_subprocess_exec", create)


@pytest.mark.asyncio
async def test_saved_nektar_record_parses_hungarian_metadata(monkeypatch, no_pacing):
    process = FakeProcess(FIXTURE.read_bytes())
    stub_process(monkeypatch, process)

    result = await oszk.lookup("9786155420818")

    assert result == provider_result.found("oszk", {
        "title": "Atomi szokások",
        "subtitle": "a jó szokások kialakításának és a rossz szokások megszüntetésének egyszerű és bizonyított módszere; apró változások, kiemelkedő eredmények",
        "authors": "James Clear",
        "publisher": "MotiBooks",
        "publish_year": 2020,
        "page_count": 377,
        "language": "hu",
    }, status=None)
    assert process.marc_path
    assert process.input == b"find @attr 1=7 9786155420818\nshow 1\nquit\n"
    no_pacing.assert_awaited_once_with("tagetes2.oszk.hu")
    assert scan_outcome.PROVIDER_LABELS["oszk"] == "OSZK NEKTÁR"
    assert browse_counts.SOURCE_LABELS["oszk"] == "OSZK NEKTÁR"


@pytest.mark.asyncio
async def test_record_for_another_edition_is_rejected(monkeypatch):
    stub_process(monkeypatch, FakeProcess(FIXTURE.read_bytes()))

    result = await oszk.lookup("9789631234567")

    assert result.outcome == "no_match"
    assert result.provider == "oszk"


@pytest.mark.asyncio
async def test_malformed_record_is_no_match_and_diagnostics_are_bounded(monkeypatch, caplog):
    diagnostic = (b"warning: diagnostic " + b"x" * 3000)
    stub_process(monkeypatch, FakeProcess(b"not a MARC record", stderr=diagnostic))

    result = await oszk.lookup("9786155420818")

    assert result.outcome == "no_match"
    assert max(map(len, caplog.messages), default=0) < 1200


@pytest.mark.asyncio
async def test_missing_yaz_client_is_transport_failure(monkeypatch, caplog):
    async def missing(*args, **kwargs):
        raise FileNotFoundError("yaz-client")

    monkeypatch.setattr(oszk.asyncio, "create_subprocess_exec", missing)

    result = await oszk.lookup("9786155420818")

    assert result.outcome == "transport_failed"
    assert result.provider == "oszk"
    assert "FileNotFoundError" in caplog.text


@pytest.mark.asyncio
async def test_query_timeout_is_transport_failure(monkeypatch):
    class SlowProcess(FakeProcess):
        async def communicate(self, _input=None):
            if self.killed:
                return b"", b""
            await asyncio.sleep(oszk.TIMEOUT + 1)

    process = SlowProcess(None)
    stub_process(monkeypatch, process)
    monkeypatch.setattr(oszk, "TIMEOUT", 0.01)

    result = await oszk.lookup("9786155420818")

    assert result.outcome == "transport_failed"
    assert process.killed


@pytest.mark.asyncio
@pytest.mark.parametrize("national_result", [
    provider_result.no_match("oszk"),
    provider_result.transport_failed("oszk"),
])
async def test_oszk_non_hit_falls_through_to_existing_metadata_provider(monkeypatch, national_result):
    from app.routers.items_common import _lookup_metadata

    ol = AsyncMock(return_value=provider_result.found("openlibrary", {"title": "OL edition"}))
    monkeypatch.setattr(oszk, "lookup", AsyncMock(return_value=national_result))
    monkeypatch.setattr("app.services.openlibrary.lookup", ol)

    metadata, source, _, _ = await _lookup_metadata("9786155420818", None, None)

    assert metadata == {"title": "OL edition"}
    assert source == "openlibrary"
    ol.assert_awaited_once()
