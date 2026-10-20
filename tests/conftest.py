"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def no_upcoming_lookups(monkeypatch, request):
    """Item pages fetch "Coming up" in the background; tests stay offline (test_upcoming
    tests the module itself with canned answers)."""
    if request.module.__name__.endswith("test_upcoming"):
        return
    from tracking import upcoming

    monkeypatch.setattr(upcoming, "for_item", lambda item, today=None: [])
