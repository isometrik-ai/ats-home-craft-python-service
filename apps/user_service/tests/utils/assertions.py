"""Assertion utilities for tests."""


def assert_success(res, status_code: int = 200):
    """Assert a standard success response and return the parsed body."""
    assert res.status_code == status_code
    body = res.json()
    assert body["status"] == "success"
    return body
