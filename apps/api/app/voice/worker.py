"""Private, versioned child-side protocol for local voice inference."""
from __future__ import annotations

from collections.abc import Callable
from multiprocessing.connection import Connection

PROTOCOL_VERSION = 1
WorkerHandler = Callable[[str, dict[str, object]], dict[str, object]]


class VoiceWorkerError(RuntimeError):
    """Typed failure crossing the private parent/child worker boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        error_type: str,
        reason: str,
        fatal: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.error_type = error_type
        self.reason = reason[:240]
        self.fatal = fatal


def serve_voice_worker(connection: Connection, handler: WorkerHandler) -> None:
    """Serve trusted Rocky messages until shutdown or parent disconnect."""

    try:
        connection.send({"protocol": PROTOCOL_VERSION, "type": "ready"})
        while True:
            try:
                message = connection.recv()
            except EOFError:
                return
            if not isinstance(message, dict):
                return
            message_type = message.get("type")
            request_id = message.get("request_id")
            if not isinstance(request_id, str):
                return
            if (
                message_type == "shutdown"
                and message.get("protocol") == PROTOCOL_VERSION
            ):
                connection.send(
                    {
                        "protocol": PROTOCOL_VERSION,
                        "type": "shutdown_ack",
                        "request_id": request_id,
                    }
                )
                return
            if (
                message_type != "request"
                or message.get("protocol") != PROTOCOL_VERSION
                or not isinstance(message.get("operation"), str)
                or not isinstance(message.get("payload"), dict)
            ):
                _send_error(
                    connection,
                    request_id=request_id,
                    error=VoiceWorkerError(
                        "Malformed worker request.",
                        code="worker_protocol_error",
                        error_type="MalformedRequest",
                        reason="The private voice worker request was invalid.",
                        fatal=True,
                    ),
                )
                return
            try:
                result = handler(
                    message["operation"],
                    message["payload"],
                )
                if not isinstance(result, dict):
                    raise VoiceWorkerError(
                        "Malformed worker result.",
                        code="worker_protocol_error",
                        error_type="MalformedResult",
                        reason="The local voice worker result was invalid.",
                        fatal=True,
                    )
            except VoiceWorkerError as exc:
                _send_error(connection, request_id=request_id, error=exc)
                if exc.fatal:
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - isolate child failures
                _send_error(
                    connection,
                    request_id=request_id,
                    error=VoiceWorkerError(
                        "Voice worker operation failed.",
                        code="worker_error",
                        error_type=exc.__class__.__name__,
                        reason="The local voice worker operation failed.",
                    ),
                )
                continue
            connection.send(
                {
                    "protocol": PROTOCOL_VERSION,
                    "type": "response",
                    "request_id": request_id,
                    "ok": True,
                    "result": result,
                }
            )
    except (BrokenPipeError, EOFError, OSError):
        return
    finally:
        connection.close()


def _send_error(
    connection: Connection,
    *,
    request_id: str,
    error: VoiceWorkerError,
) -> None:
    connection.send(
        {
            "protocol": PROTOCOL_VERSION,
            "type": "response",
            "request_id": request_id,
            "ok": False,
            "code": error.code,
            "error_type": error.error_type,
            "reason": error.reason,
            "fatal": error.fatal,
        }
    )
