"""Tests for the photo-intake tiling math and cost estimator (services/tiling.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

from app.config import (
    ANTHROPIC_HIGHRES_CAP,
    ANTHROPIC_STANDARD_CAP,
    EXPECTED_BOOKS_MAX,
    EXPECTED_BOOKS_MIN,
    TILE_OVERLAP_X,
    TILE_OVERLAP_Y,
)
from app.services import tiling

ANTHROPIC = {"vision_provider": "anthropic"}


def _overlaps(tiles):
    """Actual pixel overlap between horizontally / vertically adjacent tiles."""
    by_pos = {(t.row, t.col): t for t in tiles}
    x_overlaps, y_overlaps = [], []
    for (r, c), t in by_pos.items():
        right = by_pos.get((r, c + 1))
        if right:
            x_overlaps.append((t.x + t.w - right.x, t.w))
        below = by_pos.get((r + 1, c))
        if below:
            y_overlaps.append((t.y + t.h - below.y, t.h))
    return x_overlaps, y_overlaps


class TestIngestCap:
    def test_anthropic_default_model_is_highres(self):
        assert tiling.ingest_cap(ANTHROPIC) == ANTHROPIC_HIGHRES_CAP

    def test_anthropic_older_model_is_standard(self):
        cap = tiling.ingest_cap({**ANTHROPIC, "anthropic_vision_model": "claude-sonnet-4-6"})
        assert cap == ANTHROPIC_STANDARD_CAP

    def test_ollama_default(self):
        cap = tiling.ingest_cap({"vision_provider": "ollama"})
        assert cap["long_edge"] == 1024

    def test_ollama_setting_override(self):
        cap = tiling.ingest_cap({"vision_provider": "ollama", "ollama_ingest_long_edge": "896"})
        assert cap == {"long_edge": 896, "max_pixels": 896 * 896}


class TestDownscaleFactor:
    def test_small_image_untouched(self):
        assert tiling.downscale_factor(800, 600, ANTHROPIC_HIGHRES_CAP) == 1.0

    def test_24mp_photo(self):
        # 6000x4000: pixel budget dominates — sqrt(24M / 3.75M) ≈ 2.53
        factor = tiling.downscale_factor(6000, 4000, ANTHROPIC_HIGHRES_CAP)
        assert factor == pytest.approx(2.53, abs=0.01)

    def test_long_edge_dominates_panorama(self):
        # 12000x1400 = 16.8MP: edge factor 12000/2576 ≈ 4.66 > sqrt ratio ≈ 2.12
        factor = tiling.downscale_factor(12000, 1400, ANTHROPIC_HIGHRES_CAP)
        assert factor == pytest.approx(12000 / 2576, abs=0.01)


class TestComputeGrid:
    def test_small_image_single_tile(self):
        tiles = tiling.compute_grid(800, 600, ANTHROPIC_HIGHRES_CAP)
        assert len(tiles) == 1
        assert (tiles[0].x, tiles[0].y, tiles[0].w, tiles[0].h) == (0, 0, 800, 600)

    @pytest.mark.parametrize("w,h", [
        (6000, 4000),   # 24MP landscape
        (4000, 6000),   # 24MP portrait
        (12000, 1400),  # panorama shelf photo
        (1400, 12000),  # vertical panorama
        (2577, 2577),   # just over the edge cap
        (8192, 6144),   # 50MP
    ])
    def test_tiles_fit_cap_and_cover_image(self, w, h):
        cap = ANTHROPIC_HIGHRES_CAP
        tiles = tiling.compute_grid(w, h, cap)
        assert len(tiles) > 1
        covered_x = covered_y = 0
        for t in tiles:
            assert max(t.w, t.h) <= cap["long_edge"]
            assert t.w * t.h <= cap["max_pixels"]
            assert 0 <= t.x and 0 <= t.y
            assert t.x + t.w <= w and t.y + t.h <= h
            covered_x = max(covered_x, t.x + t.w)
            covered_y = max(covered_y, t.y + t.h)
        assert covered_x == w and covered_y == h

    def test_reading_order(self):
        tiles = tiling.compute_grid(6000, 4000, ANTHROPIC_HIGHRES_CAP)
        assert [(t.row, t.col) for t in tiles] == sorted((t.row, t.col) for t in tiles)

    def test_directional_overlap(self):
        # Vertical cuts bisect spines: generous overlap. Horizontal: modest.
        tiles = tiling.compute_grid(6000, 4000, ANTHROPIC_HIGHRES_CAP)
        x_overlaps, y_overlaps = _overlaps(tiles)
        assert x_overlaps and y_overlaps
        for overlap, tile_w in x_overlaps:
            assert overlap >= TILE_OVERLAP_X * tile_w - 2  # rounding tolerance
        for overlap, tile_h in y_overlaps:
            assert overlap >= TILE_OVERLAP_Y * tile_h - 2

    def test_panorama_is_single_row(self):
        tiles = tiling.compute_grid(12000, 1400, ANTHROPIC_HIGHRES_CAP)
        assert all(t.row == 0 for t in tiles)
        assert len(tiles) >= 5

    def test_standard_cap_splits_on_pixel_budget(self):
        # 3000x3000 fits 2 edge-tiles of 1568 each way, but 1568^2 = 2.46MP
        # exceeds the 1.15MP budget — the grid must split further.
        tiles = tiling.compute_grid(3000, 3000, ANTHROPIC_STANDARD_CAP)
        for t in tiles:
            assert t.w * t.h <= ANTHROPIC_STANDARD_CAP["max_pixels"]

    def test_no_sliver_tiles(self):
        for w, h in [(6000, 4000), (12000, 1400), (2600, 2600)]:
            tiles = tiling.compute_grid(w, h, ANTHROPIC_HIGHRES_CAP)
            for t in tiles:
                assert t.w >= 100 and t.h >= 100


class TestCostEstimator:
    def test_ollama_is_free(self):
        assert tiling.estimate_cost_usd([(6000, 4000)], {"vision_provider": "ollama"}, 50) is None

    def test_image_tokens_capped(self):
        # A 24MP source downscaled to 3.75MP would be 5000 tokens raw; capped at 4784
        tokens = tiling.image_tokens(6000, 4000, ANTHROPIC_HIGHRES_CAP, 4784)
        assert tokens == 4784

    def test_small_image_tokens_formula(self):
        assert tiling.image_tokens(750, 1000, ANTHROPIC_HIGHRES_CAP, 4784) == 1000

    def test_expected_books_clamped(self):
        assert tiling.expected_books(500, 500) == EXPECTED_BOOKS_MIN
        assert tiling.expected_books(10000, 10000) == EXPECTED_BOOKS_MAX

    def test_tiled_costs_more_input_same_output(self):
        books = 80
        as_is = tiling.estimate_cost_usd([(6000, 4000)], ANTHROPIC, books)
        tiles = tiling.compute_grid(6000, 4000, ANTHROPIC_HIGHRES_CAP)
        tiled = tiling.estimate_cost_usd([(t.w, t.h) for t in tiles], ANTHROPIC, books)
        assert 0 < as_is < tiled
        # The delta is purely input-token driven; both include the same
        # output estimate, so tiled stays within an order of magnitude.
        assert tiled < as_is * 10

    def test_pricing_comes_from_config(self):
        haiku = tiling.estimate_cost_usd(
            [(2000, 2000)], {**ANTHROPIC, "anthropic_vision_model": "claude-haiku-4-5"}, 50)
        opus = tiling.estimate_cost_usd(
            [(2000, 2000)], {**ANTHROPIC, "anthropic_vision_model": "claude-opus-4-8"}, 50)
        assert haiku < opus


class TestEvalScorer:
    @pytest.fixture()
    def eval_mod(self):
        script = Path(__file__).parent.parent / "scripts" / "eval_intake.py"
        spec = importlib.util.spec_from_file_location("eval_intake", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_scores_correct_missed_hallucinated(self, eval_mod):
        truth = json.loads((Path(__file__).parent / "fixtures" / "intake"
                            / "eleven_books.groundtruth.json").read_text())
        detected = [
            {"title": "Thinking, Fast and Slow", "authors": "Daniel Kahneman"},
            {"title": "DUNE", "authors": None},                    # case-insensitive match
            {"title": "Surely You're Joking Mr Feynman", "authors": None},  # fuzzy match
            {"title": "The Great Gatsby", "authors": None},        # hallucinated
        ]
        result = eval_mod.score(detected, truth)
        assert len(result["correct"]) == 3
        assert result["hallucinated"] == ["The Great Gatsby"]
        assert len(result["missed"]) == len(truth) - 3
