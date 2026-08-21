"""Smoke tests for generated notification gRPC stubs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import grpc
import pytest

from libs.grpc_stubs.notification import notification_service_pb2 as pb2
from libs.grpc_stubs.notification.notification_service_pb2_grpc import (
    Greeter,
    GreeterServicer,
    GreeterStub,
    add_GreeterServicer_to_server,
)


def test_notification_request_and_reply_messages():
    req = pb2.NotificationRequest(body_data='{"title":"Hi"}')
    assert req.body_data == '{"title":"Hi"}'

    reply = pb2.NotificationReply(message="ok")
    assert reply.message == "ok"


def test_greeter_stub_binds_unary_unary():
    channel = MagicMock()
    stub = GreeterStub(channel)
    channel.unary_unary.assert_called_once()
    assert stub.SendNotification is not None


def test_greeter_servicer_send_notification_unimplemented():
    servicer = GreeterServicer()
    context = MagicMock()
    with pytest.raises(NotImplementedError):
        servicer.SendNotification(pb2.NotificationRequest(), context)
    context.set_code.assert_called_once_with(grpc.StatusCode.UNIMPLEMENTED)


def test_add_greeter_servicer_to_server():
    server = MagicMock()
    servicer = GreeterServicer()
    add_GreeterServicer_to_server(servicer, server)
    assert server.add_generic_rpc_handlers.called
    assert server.add_registered_method_handlers.called


def test_greeter_experimental_send_notification():
    with patch("grpc.experimental.unary_unary", return_value="sent") as unary:
        result = Greeter.SendNotification(
            pb2.NotificationRequest(body_data="x"),
            "localhost:50051",
            insecure=True,
        )
    assert result == "sent"
    unary.assert_called_once()


def test_pb2_module_has_descriptor():
    assert pb2.DESCRIPTOR is not None
    assert pb2.NotificationRequest is not None


def test_grpc_stub_import_error_branch():
    """Cover ImportError fallback when grpc._utilities is unavailable."""
    import importlib
    import sys

    mod_name = "libs.grpc_stubs.notification.notification_service_pb2_grpc"
    saved = sys.modules.pop(mod_name)
    original_import = __import__

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "grpc._utilities":
            raise ImportError("forced for coverage")
        return original_import(name, globals, locals, fromlist, level)

    try:
        with patch("builtins.__import__", side_effect=_import):
            with pytest.raises(RuntimeError, match="grpc package installed"):
                importlib.import_module(mod_name)
    finally:
        sys.modules[mod_name] = saved


def test_grpc_stub_raises_when_version_unsupported():
    """Cover RuntimeError when installed grpc is older than generated version."""
    import importlib
    import sys

    mod_name = "libs.grpc_stubs.notification.notification_service_pb2_grpc"
    saved = sys.modules.pop(mod_name)

    try:
        with patch(
            "grpc._utilities.first_version_is_lower",
            return_value=True,
        ):
            with pytest.raises(RuntimeError, match="grpc package installed"):
                importlib.import_module(mod_name)
    finally:
        sys.modules[mod_name] = saved
