"""Tests for app.services.code128 — pure encoding, no I/O."""

import pytest

from app.services.code128 import _PATTERNS, encode, modules, svg, values


def test_pattern_table_shape():
    assert len(_PATTERNS) == 107
    # Every symbol is 11 modules wide; STOP is 13 including the final bar.
    assert all(sum(map(int, p)) == 11 for p in _PATTERNS[:106])
    assert sum(map(int, _PATTERNS[106])) == 13


def test_start_b_and_stop_patterns():
    widths = encode("A")
    assert widths[:6] == [2, 1, 1, 2, 1, 4]
    assert widths[-7:] == [2, 3, 3, 1, 1, 1, 2]


def test_copy_code_switches_to_set_c():
    # 104 + 1*51 + 2*40 + 3*38 + 4*13 + 5*99 + 6*0 + 7*0 + 8*12 = 992
    checksum = 992 % 103
    assert checksum == 65
    assert values("SHF-000012") == [104, 51, 40, 38, 13, 99, 0, 0, 12, checksum, 106]


def test_b_only_string_stays_in_b():
    v = values("AB-123")  # 3-digit run: too short and odd
    assert 99 not in v and 105 not in v
    assert v[0] == 104


def test_odd_digit_run_stays_in_b():
    assert 99 not in values("SHF-00001")


def test_modules_is_sum_of_widths():
    assert modules("SHF-000012") == sum(encode("SHF-000012"))
    assert modules("SHF-000012") == 11 * 10 + 13  # 9 symbols + checksum, plus stop


def test_svg_is_self_contained_and_rect_count_exact():
    out = svg("SHF-000012", height_mm=10, module_mm=0.3)
    assert "<script" not in out
    assert "href=" not in out
    assert "xlink" not in out
    bars = len(encode("SHF-000012")[::2])
    # One background rect plus one rect per bar.
    assert out.count("<rect") == bars + 1
    assert "<text" in out


def test_svg_without_text_and_escaping():
    assert "<text" not in svg("A", height_mm=8, module_mm=0.3, with_text=False)
    out = svg("A&B", height_mm=8, module_mm=0.3)
    assert "A&amp;B" in out and "A&B<" not in out


@pytest.mark.parametrize("bad", ["SHF-é1", "日本", "a\tb"])
def test_non_set_b_character_raises(bad):
    with pytest.raises(ValueError):
        encode(bad)


def test_empty_raises():
    with pytest.raises(ValueError):
        encode("")
