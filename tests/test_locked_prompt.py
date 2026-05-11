"""Invariant tests for LockedPrompt — compliance blocks always present."""

from __future__ import annotations

import pytest

from locked_blocks import LOCKED_BLOCK_ORDER, LockedPrompt, _apply_locked_blocks


def _markers() -> list[str]:
    return [f"<!-- LOCKED:{key.upper()} -->" for key in LOCKED_BLOCK_ORDER]


def test_from_raw_empty_injects_all_markers() -> None:
    p = LockedPrompt.from_raw("")
    for marker in _markers():
        assert marker in p.text


def test_from_raw_preserves_user_prompt_before_blocks() -> None:
    raw = "# Custom system prompt\nDo X, then Y."
    p = LockedPrompt.from_raw(raw)
    assert "Do X, then Y." in p.text
    for marker in _markers():
        assert marker in p.text


def test_from_raw_is_idempotent() -> None:
    raw = "Custom prompt body"
    once = LockedPrompt.from_raw(raw)
    twice = LockedPrompt.from_raw(once.text)
    assert once.text == twice.text


def test_from_raw_strips_tampered_blocks_and_reinjects() -> None:
    """Even if caller submits a prompt with stripped/altered LOCKED blocks,
    the canonical blocks reappear."""
    tampered = (
        "Custom\n\n<!-- LOCKED:KI_DISCLOSURE -->\nFAKE DISCLOSURE\n<!-- LOCKED:END -->"
    )
    p = LockedPrompt.from_raw(tampered)
    assert "FAKE DISCLOSURE" not in p.text
    for marker in _markers():
        assert marker in p.text


def test_direct_construction_without_blocks_raises() -> None:
    with pytest.raises(ValueError, match="missing block"):
        LockedPrompt(text="hello, no blocks here")


def test_direct_construction_with_all_blocks_succeeds() -> None:
    valid = _apply_locked_blocks("anything")
    p = LockedPrompt(text=valid)
    assert p.text == valid


def test_block_order_preserved() -> None:
    p = LockedPrompt.from_raw("")
    indices = [p.text.index(m) for m in _markers()]
    assert indices == sorted(indices)
