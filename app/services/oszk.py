"""OSZK NEKTÁR HUNMARC records over Z39.50 for Hungarian ISBNs."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from pymarc import MARCReader

from app.services import bib_normalize, outbound, provider_result

logger = logging.getLogger(__name__)

HOST = "tagetes2.oszk.hu"
ADDRESS = f"{HOST}:1616/ANY"
TIMEOUT = 5
_ISBN13 = re.compile(r"(?<!\d)97[89](?:[ -]?\d){10}(?!\d)")


def _diagnostic(stdout: bytes, stderr: bytes) -> str:
    """Keep useful YAZ status/error lines, never dump MARC record contents."""
    lines = (stderr + b"\n" + stdout).decode("utf-8", errors="replace").splitlines()
    selected = [
        line.strip()[:180]
        for line in lines
        if any(
            word in line.casefold()
            for word in (
                "error", "diagnostic", "warning", "search", "hits", "connection", "connecting"
            )
        )
    ]
    return " | ".join(selected[:6])[:900]


def _matches_isbn(record, isbn13: str) -> bool:
    for field in record.get_fields("020"):
        for value in field.get_subfields("a"):
            for match in _ISBN13.finditer(value):
                if re.sub(r"\D", "", match.group()) == isbn13:
                    return True
    return False


def _field_text(record, tag: str, code: str) -> list[str]:
    return [
        bib_normalize.nfc(value)
        for field in record.get_fields(tag)
        for value in field.get_subfields(code)
        if value.strip()
    ]


def _parse_record(record) -> dict | None:
    titles = _field_text(record, "245", "a")
    if not titles:
        return None
    result: dict = {"title": titles[0]}

    subtitles = _field_text(record, "245", "b")
    if subtitles:
        result["subtitle"] = "; ".join(subtitles)

    names = []
    for field in record.get_fields("100"):
        surname = bib_normalize.nfc(field.get("a", "")).rstrip(" ,")
        given = bib_normalize.nfc(field.get("j", ""))
        name = (
            f"{given} {surname}".strip()
            if given else bib_normalize.invert_name(surname)
        )
        if name:
            names.append(name)
    if names:
        result["authors"] = ", ".join(dict.fromkeys(names))

    publication = record.get_fields("264") or record.get_fields("260")
    if publication:
        publishers = [
            bib_normalize.nfc(value)
            for value in publication[0].get_subfields("b")
            if value.strip()
        ]
        if publishers:
            result["publisher"] = publishers[0]
        dates = publication[0].get_subfields("c")
        year = bib_normalize.first_year(" ".join(dates))
        if year is not None:
            result["publish_year"] = year

    extents = _field_text(record, "300", "a")
    if extents:
        pages = bib_normalize.leading_int(extents[0])
        if pages is not None:
            result["page_count"] = pages

    languages = _field_text(record, "041", "a")
    if not languages:
        fields_008 = record.get_fields("008")
        if fields_008 and len(str(fields_008[0].data)) >= 38:
            language = str(fields_008[0].data)[35:38].strip()
            languages = [language] if language else []
    if languages:
        result["language"] = bib_normalize.to_iso639_1(languages[0])

    for field in record.get_fields("020"):
        for value in field.get_subfields("a"):
            candidate = re.sub(r"[^\dXx]", "", value).upper()
            if len(candidate) == 10:
                result["isbn10"] = candidate
                return result
    return result


async def lookup(isbn13: str, client=None) -> provider_result.ProviderResult:
    """Query NEKTÁR, returning only the exact-edition record as metadata."""
    if not re.fullmatch(r"[0-9]{13}", isbn13):
        return provider_result.no_match("oszk")
    await outbound.acquire(HOST)
    with tempfile.TemporaryDirectory(prefix="shelf-oszk-") as directory:
        marc_path = Path(directory) / "record.mrc"
        try:
            process = await asyncio.create_subprocess_exec(
                "yaz-client", "-m", str(marc_path), ADDRESS,
                cwd=directory,
                env={"HOME": directory, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.warning(
                "OSZK NEKTÁR client unavailable for ISBN %s: %s",
                isbn13, type(exc).__name__,
            )
            return provider_result.transport_failed("oszk")

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(
                    f"find @attr 1=7 {isbn13}\nshow 1\nquit\n".encode("ascii")
                ),
                timeout=TIMEOUT,
            )
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            logger.warning(
                "OSZK NEKTÁR query timed out for ISBN %s: %s",
                isbn13, _diagnostic(stdout, stderr),
            )
            return provider_result.transport_failed("oszk")

        diagnostic = _diagnostic(stdout, stderr)
        if process.returncode:
            logger.warning(
                "OSZK NEKTÁR query failed for ISBN %s (exit %s): %s",
                isbn13, process.returncode, diagnostic,
            )
            return provider_result.transport_failed("oszk")

        try:
            with marc_path.open("rb") as stream:
                for record in MARCReader(stream, to_unicode=True, force_utf8=True):
                    if record and _matches_isbn(record, isbn13):
                        metadata = _parse_record(record)
                        if metadata:
                            return provider_result.found("oszk", metadata, status=None)
        except Exception as exc:
            logger.warning(
                "OSZK NEKTÁR malformed MARC for ISBN %s (%s): %s",
                isbn13, type(exc).__name__, diagnostic,
            )
            return provider_result.no_match("oszk")

        if diagnostic:
            logger.debug("OSZK NEKTÁR no exact record for ISBN %s: %s", isbn13, diagnostic)
        return provider_result.no_match("oszk")
