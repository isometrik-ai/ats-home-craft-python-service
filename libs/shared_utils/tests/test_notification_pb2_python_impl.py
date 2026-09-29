"""Import notification pb2 under pure-Python protobuf when available."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_notification_pb2_import_under_python_implementation():
    """Exercise pb2 generated options path when pure-Python protobuf is used."""
    env = os.environ.copy()
    env["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    script = """
from libs.grpc_stubs.notification import notification_service_pb2 as pb2
req = pb2.NotificationRequest(body_data="payload")
assert req.body_data == "payload"
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 and "pure-Python" in (completed.stderr or ""):
        pytest.skip("Pure-Python protobuf implementation unavailable")
    assert completed.returncode == 0, completed.stderr
