from typing import Any, Optional


class NoctHostError(Exception):
    """Raised for any failed call.

    Attributes:
        status: HTTP status (401 bad or revoked key, 429 rate limited, ...),
            or 0 when no HTTP response was received or a helper such as
            ``wait_for_active`` gave up.
        code: short machine readable reason: "http", "network", "timeout",
            "wait_timeout", "server_failed" or "missing_api_key".
        body: parsed JSON error body when the API sent one.
        retry_after: seconds from the Retry-After header, when present.
    """

    def __init__(
        self,
        message: str,
        status: int = 0,
        code: Optional[str] = None,
        body: Any = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code or ("http" if status else "network")
        self.body = body
        self.retry_after = retry_after

    def __repr__(self) -> str:
        return "NoctHostError(status={!r}, code={!r}, message={!r})".format(
            self.status, self.code, self.message
        )
