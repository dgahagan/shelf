"""Tiling math and cost estimation for high-resolution photo intake.

Pure functions, no I/O. The grid geometry is computed server-side so it is
testable and provider caps stay out of the UI; the actual pixel cropping
happens client-side (Canvas API) so the full-resolution original never has
to be uploaded.
"""

import math
from dataclasses import dataclass

from app.config import (
    ANTHROPIC_HIGHRES_CAP,
    ANTHROPIC_HIGHRES_IMAGE_TOKEN_CAP,
    ANTHROPIC_HIGHRES_MODELS,
    ANTHROPIC_STANDARD_CAP,
    ANTHROPIC_STANDARD_IMAGE_TOKEN_CAP,
    EXPECTED_BOOKS_MAX,
    EXPECTED_BOOKS_MIN,
    EXPECTED_BOOKS_PER_MEGAPIXEL,
    IMAGE_TOKEN_DIVISOR,
    OLLAMA_DEFAULT_INGEST_LONG_EDGE,
    PROMPT_OVERHEAD_TOKENS,
    TILE_OVERLAP_X,
    TILE_OVERLAP_Y,
    TOKENS_PER_BOOK,
    VISION_PRICING,
    VISION_PRICING_DEFAULT,
)
from app.services.vision import DEFAULT_ANTHROPIC_MODEL


@dataclass
class Tile:
    x: int
    y: int
    w: int
    h: int
    row: int
    col: int


def ingest_cap(settings: dict) -> dict:
    """Resolution cap for the active provider: {long_edge, max_pixels}."""
    provider = settings.get("vision_provider") or ""
    if provider == "anthropic":
        model = settings.get("anthropic_vision_model") or DEFAULT_ANTHROPIC_MODEL
        if any(m in model for m in ANTHROPIC_HIGHRES_MODELS):
            return dict(ANTHROPIC_HIGHRES_CAP)
        return dict(ANTHROPIC_STANDARD_CAP)
    # Ollama: we control resolution entirely; treat the knob as the long edge
    long_edge = int(settings.get("ollama_ingest_long_edge") or OLLAMA_DEFAULT_INGEST_LONG_EDGE)
    return {"long_edge": long_edge, "max_pixels": long_edge * long_edge}


def scaled_dims(width: int, height: int, cap: dict) -> tuple[int, int]:
    """Dimensions after the provider downscales to fit the cap."""
    factor = downscale_factor(width, height, cap)
    if factor <= 1.0:
        return width, height
    return max(1, round(width / factor)), max(1, round(height / factor))


def downscale_factor(width: int, height: int, cap: dict) -> float:
    """How much the provider will shrink this image (1.0 = untouched)."""
    long_edge = max(width, height)
    factor = max(1.0, long_edge / cap["long_edge"])
    if cap.get("max_pixels"):
        factor = max(factor, math.sqrt(width * height / cap["max_pixels"]))
    return factor


def compute_grid(
    width: int,
    height: int,
    cap: dict,
    overlap_x: float = TILE_OVERLAP_X,
    overlap_y: float = TILE_OVERLAP_Y,
) -> list[Tile]:
    """Grid of overlapping full-resolution tiles, each within the cap.

    Ordering is left-to-right, top-to-bottom (communicated to the model in
    the prompt). Vertical cut lines bisect spines so they get generous
    overlap; horizontal cuts get a modest one. Edge tiles absorb rounding
    remainders so the union of tiles always covers the whole image.
    """
    if downscale_factor(width, height, cap) <= 1.0:
        return [Tile(x=0, y=0, w=width, h=height, row=0, col=0)]

    cols = _axis_tile_count(width, cap["long_edge"], overlap_x)
    rows = _axis_tile_count(height, cap["long_edge"], overlap_y)
    # A long_edge x long_edge tile can still exceed max_pixels caps that are
    # below long_edge^2 (e.g. 1568px/1.15MP); split further until tiles fit.
    while _tile_pixels(width, cols, overlap_x) * _tile_pixels(height, rows, overlap_y) > cap["max_pixels"]:
        if _tile_pixels(width, cols, overlap_x) >= _tile_pixels(height, rows, overlap_y):
            cols += 1
        else:
            rows += 1

    tile_w = _tile_pixels(width, cols, overlap_x)
    tile_h = _tile_pixels(height, rows, overlap_y)
    step_x = (width - tile_w) / (cols - 1) if cols > 1 else 0
    step_y = (height - tile_h) / (rows - 1) if rows > 1 else 0

    tiles = []
    for r in range(rows):
        y = round(r * step_y)
        for c in range(cols):
            x = round(c * step_x)
            tiles.append(Tile(
                x=x, y=y,
                w=min(tile_w, width - x), h=min(tile_h, height - y),
                row=r, col=c,
            ))
    return tiles


def _axis_tile_count(length: int, cap_edge: int, overlap: float) -> int:
    """Minimum tiles along one axis so each tile fits cap_edge with overlap."""
    if length <= cap_edge:
        return 1
    # n tiles of size s with pairwise overlap o*s cover n*s - (n-1)*o*s
    effective = cap_edge * (1 - overlap)
    return max(1, math.ceil((length - cap_edge * overlap) / effective))


def _tile_pixels(length: int, n: int, overlap: float) -> int:
    """Tile size along one axis for n tiles covering `length` with overlap."""
    if n == 1:
        return length
    # length = n*s - (n-1)*overlap*s  =>  s = length / (n - (n-1)*overlap)
    return math.ceil(length / (n - (n - 1) * overlap))


def image_tokens(width: int, height: int, cap: dict, token_cap: int) -> int:
    """Approximate input tokens for one image after provider downscale."""
    w, h = scaled_dims(width, height, cap)
    return min(math.ceil(w * h / IMAGE_TOKEN_DIVISOR), token_cap)


def expected_books(width: int, height: int) -> int:
    """Rough book-count estimate from source area (drives the output estimate)."""
    megapixels = width * height / 1_000_000
    est = round(megapixels * EXPECTED_BOOKS_PER_MEGAPIXEL)
    return max(EXPECTED_BOOKS_MIN, min(EXPECTED_BOOKS_MAX, est))


def estimate_cost_usd(images_dims: list[tuple[int, int]], settings: dict, books: int) -> float | None:
    """Estimated dollars for one analyze call. None for local providers (free)."""
    if (settings.get("vision_provider") or "") != "anthropic":
        return None
    model = settings.get("anthropic_vision_model") or DEFAULT_ANTHROPIC_MODEL
    cap = ingest_cap(settings)
    token_cap = (
        ANTHROPIC_HIGHRES_IMAGE_TOKEN_CAP
        if any(m in model for m in ANTHROPIC_HIGHRES_MODELS)
        else ANTHROPIC_STANDARD_IMAGE_TOKEN_CAP
    )
    input_tokens = PROMPT_OVERHEAD_TOKENS + sum(
        image_tokens(w, h, cap, token_cap) for w, h in images_dims
    )
    output_tokens = books * TOKENS_PER_BOOK
    in_price, out_price = VISION_PRICING_DEFAULT
    for prefix, prices in VISION_PRICING.items():
        if prefix in model:
            in_price, out_price = prices
            break
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
