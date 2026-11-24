"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests offline even if a developer's .env holds a real GEMINI_API_KEY.

    Tests that exercise the Gemini path set a fake key explicitly with patch.dict.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
