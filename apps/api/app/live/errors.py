"""Live-intelligence error types."""
from __future__ import annotations


class LiveError(RuntimeError):
    def __init__(self, message: str, *, code: str = "live_error") -> None:
        super().__init__(message)
        self.code = code


class LiveProviderUnavailable(LiveError):
    def __init__(self, message: str = "Live provider is unavailable.") -> None:
        super().__init__(message, code="provider_unavailable")


class LiveProviderTimeout(LiveError):
    def __init__(self, message: str = "Live provider timed out.") -> None:
        super().__init__(message, code="provider_timeout")


class LiveMalformedResult(LiveError):
    def __init__(self, message: str = "Live provider returned malformed data.") -> None:
        super().__init__(message, code="malformed_result")


class LiveUnsupportedRequest(LiveError):
    def __init__(self, message: str = "That live lookup is not supported yet.") -> None:
        super().__init__(message, code="unsupported_request")
