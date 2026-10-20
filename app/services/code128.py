"""Code 128 barcode encoder and SVG renderer for copy labels.

Copy codes read ``PREFIX-NNNNNN`` and are printed on label sheets. Code 128 is
the symbology every handheld scanner reads without configuration, and a
hand-rolled encoder keeps the print page free of JS and external assets: the
strict CSP allows inline SVG ``<rect>`` elements and nothing else is needed.

Pure functions, no I/O. Code Set B covers the prefix and hyphen. A trailing run
of digits switches to Code Set C (two digits per symbol) when it is at least
four digits long and of even length, which halves the width of the number.
Anything else stays in Code Set B.
"""

from html import escape

# Bar/space widths (bar first) for symbol values 0-106. Value 106 is STOP and
# carries the final termination bar, so it has 7 widths; the rest have 6.
_PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213",
    "122312", "132212", "221213", "221312", "231212", "112232", "122132",
    "122231", "113222", "123122", "123221", "223211", "221132", "221231",
    "213212", "223112", "312131", "311222", "321122", "321221", "312212",
    "322112", "322211", "212123", "212321", "232121", "111323", "131123",
    "131321", "112313", "132113", "132311", "211313", "231113", "231311",
    "112133", "112331", "132131", "113123", "113321", "133121", "313121",
    "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212",
    "124112", "124211", "411212", "421112", "421211", "212141", "214121",
    "412121", "111143", "111341", "131141", "114113", "114311", "411113",
    "411311", "113141", "114131", "311141", "411131", "211412", "211214",
    "211232", "2331112",
)

START_B = 104
START_C = 105
CODE_C = 99  # switch B -> C
STOP = 106


def _use_set_c(text: str) -> int:
    """Return the index where the Code Set C digit run starts, or -1.

    The run must be trailing, at least 4 digits, and of even length.
    """
    end = len(text)
    start = end
    while start > 0 and text[start - 1].isascii() and text[start - 1].isdigit():
        start -= 1
    run = end - start
    return start if run >= 4 and run % 2 == 0 else -1


def values(text: str) -> list[int]:
    """Symbol values for ``text``: start, data, checksum, stop.

    Raises ``ValueError`` for a character outside Code Set B (ASCII 32-126).
    """
    if not text:
        raise ValueError("empty string")
    for ch in text:
        if not 32 <= ord(ch) <= 126:
            raise ValueError(f"character {ch!r} is not in Code Set B")
    c_start = _use_set_c(text)
    b_part = text if c_start < 0 else text[:c_start]
    out = [START_B] + [ord(ch) - 32 for ch in b_part]
    if c_start >= 0:
        out.append(CODE_C)
        digits = text[c_start:]
        out += [int(digits[i:i + 2]) for i in range(0, len(digits), 2)]
    # Checksum: start value plus each data symbol times its 1-based position.
    total = out[0] + sum(v * i for i, v in enumerate(out[1:], start=1))
    out.append(total % 103)
    out.append(STOP)
    return out


def encode(text: str) -> list[int]:
    """Module-width sequence (bar, space, bar, ...) for ``text``.

    Six widths per symbol; the STOP symbol contributes seven.
    """
    return [int(c) for v in values(text) for c in _PATTERNS[v]]


def modules(text: str) -> int:
    """Total symbol width in modules, excluding quiet zones."""
    return sum(encode(text))


def svg(text: str, *, height_mm: float, module_mm: float, with_text: bool = True) -> str:
    """Render ``text`` as one self-contained ``<svg>`` of ``<rect>`` bars.

    The output has a white background rect (the first rect) followed by one
    rect per bar. It does NOT include a quiet zone: the caller's layout must
    leave at least 10 modules of blank space on each side. No scripts, links
    or external references are emitted.
    """
    widths = encode(text)
    total = sum(widths)
    width_mm = total * module_mm
    text_mm = height_mm * 0.22 if with_text else 0.0
    bar_h = height_mm - text_mm
    rects = [f'<rect width="{width_mm:.3f}" height="{height_mm:.3f}" fill="#fff"/>']
    x = 0
    for i, w in enumerate(widths):
        if i % 2 == 0:  # even positions are bars, odd are spaces
            rects.append(
                f'<rect x="{x * module_mm:.3f}" y="0" '
                f'width="{w * module_mm:.3f}" height="{bar_h:.3f}" fill="#000"/>'
            )
        x += w
    label = ""
    if with_text:
        label = (
            f'<text x="{width_mm / 2:.3f}" y="{height_mm - text_mm * 0.15:.3f}" '
            f'font-family="monospace" font-size="{text_mm * 0.85:.3f}" '
            f'text-anchor="middle" fill="#000">{escape(text)}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.3f}mm" '
        f'height="{height_mm:.3f}mm" viewBox="0 0 {width_mm:.3f} {height_mm:.3f}" '
        f'role="img" aria-label="{escape(text)}">' + "".join(rects) + label + "</svg>"
    )
