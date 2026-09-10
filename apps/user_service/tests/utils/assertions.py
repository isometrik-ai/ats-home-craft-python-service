"""Assertion utilities for tests."""


def assert_success(res, status_code: int = 200):
    """Assert a standard success response and return the parsed body."""
    assert res.status_code == status_code
    body = res.json()
    assert body["status"] == "success"
    return body


def assert_error(res, status_code: int = 400, *, message_fragment: str | None = None):
    """Assert a standard error response and return the parsed body."""
    assert res.status_code == status_code
    body = res.json()
    assert body["status"] == "error"
    if message_fragment is not None:
        assert message_fragment in body["message"]
    return body
