"""Five-level readable-font preference stays local and persistent."""

from __future__ import annotations

import pytest

from src.font_size_preferences import (
    DEFAULT_LEVEL,
    font_size_css,
    read_font_level,
    write_font_level,
)


def test_font_size_round_trip_and_css(tmp_path) -> None:
    assert read_font_level(tmp_path) == DEFAULT_LEVEL
    write_font_level(tmp_path, 5)
    assert read_font_level(tmp_path) == 5
    assert "23px" in font_size_css(5)
    assert ".katex" in font_size_css(5)


@pytest.mark.parametrize("bad", [0, 6, 2.5, True])
def test_reject_bad_font_levels(tmp_path, bad) -> None:
    with pytest.raises(ValueError):
        write_font_level(tmp_path, bad)
