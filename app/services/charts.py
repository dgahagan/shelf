"""Server-rendered SVG charts for the stats dashboard.

Pure functions returning SVG markup strings (templates insert them with
|safe). All label text passes through markupsafe.escape — author names and
other user data reach SVG text nodes. No JS: hover uses native <title>
tooltips, which keeps the page fully CSP-compliant.

Visual spec (see docs/plans/STATS_DASHBOARD.md): single-hue marks in the
brand accent (validated against the dark surface), bars <=24px with 4px
rounded data-ends, 2px lines, 10%-opacity area fills, hairline gridlines,
selective labels in text tokens.
"""
from markupsafe import escape

MARK = "#6366f1"        # validated vs card surface #1a1d27
GRID = "#262b3d"        # hairline, one step off the surface
TEXT_MUTED = "#94a3b8"
TEXT = "#e2e8f0"

_FONT = 'font-family="inherit"'


def _nice_step(max_value: float) -> float:
    """A clean y-axis step (1/2/2.5/5 x 10^n) giving ~4 gridlines."""
    if max_value <= 0:
        return 1
    raw = max_value / 4
    magnitude = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * magnitude:
            return mult * magnitude
    return 10 * magnitude


def _fmt(v: float) -> str:
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}"


def _empty(width: int, height: int, message: str) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" {_FONT}>'
        f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
        f'fill="{TEXT_MUTED}" font-size="13">{escape(message)}</text></svg>'
    )


def _grid_and_ticks(max_v: float, x0: int, x1: int, y0: int, y1: int, value_prefix: str = "") -> tuple[str, float]:
    """Horizontal hairlines + y tick labels. Returns (svg, scale_max)."""
    step = _nice_step(max_v)
    scale_max = step
    while scale_max < max_v:
        scale_max += step
    parts = []
    v = 0.0
    while v <= scale_max:
        y = y1 - (v / scale_max) * (y1 - y0)
        if v > 0:  # baseline drawn by the axis itself
            parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(
            f'<text x="{x0 - 6}" y="{y + 3.5:.1f}" text-anchor="end" fill="{TEXT_MUTED}" '
            f'font-size="10">{value_prefix}{_fmt(v)}</text>'
        )
        v += step
    parts.append(f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="{GRID}" stroke-width="1"/>')
    return "".join(parts), scale_max


def _rounded_top_bar(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Bar path: 4px rounded data-end (top), square at the baseline."""
    if h <= r:
        r = max(h / 2, 1)
    return (
        f'M {x:.1f} {y + h:.1f} L {x:.1f} {y + r:.1f} Q {x:.1f} {y:.1f} {x + r:.1f} {y:.1f} '
        f'L {x + w - r:.1f} {y:.1f} Q {x + w:.1f} {y:.1f} {x + w:.1f} {y + r:.1f} '
        f'L {x + w:.1f} {y + h:.1f} Z'
    )


def column_chart(pairs: list[tuple[str, float]], width: int = 460, height: int = 200,
                 value_prefix: str = "", empty_message: str = "No data yet") -> str:
    """Vertical columns for (label, value) pairs, e.g. books read per year."""
    if not pairs or all(v == 0 for _, v in pairs):
        return _empty(width, height, empty_message)

    pad_l, pad_r, pad_t, pad_b = 44, 8, 14, 22
    x0, x1, y0, y1 = pad_l, width - pad_r, pad_t, height - pad_b
    max_v = max(v for _, v in pairs)
    grid, scale_max = _grid_and_ticks(max_v, x0, x1, y0, y1, value_prefix)

    n = len(pairs)
    band = (x1 - x0) / n
    bar_w = min(24.0, band * 0.6)
    max_idx = max(range(n), key=lambda i: pairs[i][1])

    marks, labels = [], []
    for i, (label, v) in enumerate(pairs):
        bx = x0 + i * band + (band - bar_w) / 2
        bh = (v / scale_max) * (y1 - y0)
        by = y1 - bh
        title = f"{label}: {value_prefix}{_fmt(v)}"
        if v > 0:
            marks.append(
                f'<path d="{_rounded_top_bar(bx, by, bar_w, bh)}" fill="{MARK}">'
                f'<title>{escape(title)}</title></path>'
            )
        # x labels: thin out when crowded
        show_every = max(1, n // 8)
        if i % show_every == 0 or i == n - 1:
            labels.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{y1 + 14}" text-anchor="middle" '
                f'fill="{TEXT_MUTED}" font-size="10">{escape(str(label))}</text>'
            )
        # value labels: only the max and the latest column
        if v > 0 and (i == max_idx or i == n - 1):
            labels.append(
                f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
                f'fill="{TEXT}" font-size="10">{value_prefix}{_fmt(v)}</text>'
            )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" {_FONT}>'
        f'{grid}{"".join(marks)}{"".join(labels)}</svg>'
    )


def area_chart(points: list[tuple[str, float]], width: int = 460, height: int = 200,
               value_prefix: str = "", empty_message: str = "No data yet") -> str:
    """Single-series area/line, e.g. cumulative collection growth."""
    if not points:
        return _empty(width, height, empty_message)
    if len(points) == 1:
        points = points + points  # a lone point still draws a flat line

    pad_l, pad_r, pad_t, pad_b = 44, 46, 14, 22
    x0, x1, y0, y1 = pad_l, width - pad_r, pad_t, height - pad_b
    max_v = max(v for _, v in points)
    if max_v <= 0:
        return _empty(width, height, empty_message)
    grid, scale_max = _grid_and_ticks(max_v, x0, x1, y0, y1, value_prefix)

    n = len(points)
    coords = []
    for i, (_, v) in enumerate(points):
        px = x0 + (i / (n - 1)) * (x1 - x0)
        py = y1 - (v / scale_max) * (y1 - y0)
        coords.append((px, py))

    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{x0:.1f},{y1} " + line + f" {x1:.1f},{y1}"

    # x labels: first, middle, last
    xlabels = []
    for i in {0, (n - 1) // 2, n - 1}:
        px = x0 + (i / (n - 1)) * (x1 - x0)
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        xlabels.append(
            f'<text x="{px:.1f}" y="{y1 + 14}" text-anchor="{anchor}" '
            f'fill="{TEXT_MUTED}" font-size="10">{escape(str(points[i][0]))}</text>'
        )

    end_x, end_y = coords[-1]
    end_label, end_value = points[-1]
    hover = (
        f'<polyline points="{line}" fill="none" stroke="transparent" stroke-width="14">'
        f'<title>{escape(f"{end_label}: {value_prefix}{_fmt(end_value)} (latest)")}</title></polyline>'
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" {_FONT}>'
        f'{grid}'
        f'<polygon points="{area}" fill="{MARK}" opacity="0.1"/>'
        f'<polyline points="{line}" fill="none" stroke="{MARK}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'{hover}'
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" fill="{MARK}" stroke="#1a1d27" stroke-width="2"/>'
        f'<text x="{end_x + 8:.1f}" y="{end_y + 3.5:.1f}" fill="{TEXT}" font-size="10">'
        f'{value_prefix}{_fmt(end_value)}</text>'
        f'{"".join(xlabels)}</svg>'
    )


def hbar_chart(pairs: list[tuple[str, float]], width: int = 460,
               value_prefix: str = "", empty_message: str = "No data yet") -> str:
    """Horizontal bars for (label, value), e.g. top authors. Height adapts."""
    if not pairs:
        return _empty(width, 120, empty_message)

    row_h, bar_h = 26, 14
    pad_t, pad_b = 4, 4
    height = pad_t + row_h * len(pairs) + pad_b
    label_w = 150
    x0, x1 = label_w + 8, width - 44
    max_v = max(v for _, v in pairs) or 1

    parts = []
    for i, (label, v) in enumerate(pairs):
        cy = pad_t + i * row_h + row_h / 2
        bw = max((v / max_v) * (x1 - x0), 2)
        by = cy - bar_h / 2
        # rounded right data-end, square left baseline (rotate the bar spec)
        r = 4 if bw > 8 else 1
        path = (
            f'M {x0} {by:.1f} L {x0 + bw - r:.1f} {by:.1f} Q {x0 + bw:.1f} {by:.1f} {x0 + bw:.1f} {by + r:.1f} '
            f'L {x0 + bw:.1f} {by + bar_h - r:.1f} Q {x0 + bw:.1f} {by + bar_h:.1f} {x0 + bw - r:.1f} {by + bar_h:.1f} '
            f'L {x0} {by + bar_h:.1f} Z'
        )
        shown = str(label) if len(str(label)) <= 24 else str(label)[:23] + "…"
        parts.append(
            f'<text x="{label_w}" y="{cy + 3.5:.1f}" text-anchor="end" fill="{TEXT_MUTED}" '
            f'font-size="11">{escape(shown)}</text>'
            f'<path d="{path}" fill="{MARK}"><title>{escape(f"{label}: {value_prefix}{_fmt(v)}")}</title></path>'
            f'<text x="{x0 + bw + 6:.1f}" y="{cy + 3.5:.1f}" fill="{TEXT}" '
            f'font-size="11">{value_prefix}{_fmt(v)}</text>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" {_FONT}>'
        f'<line x1="{x0}" y1="{pad_t}" x2="{x0}" y2="{height - pad_b}" stroke="{GRID}" stroke-width="1"/>'
        f'{"".join(parts)}</svg>'
    )
