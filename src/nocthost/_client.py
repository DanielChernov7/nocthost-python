import json
import os
import socket
import time
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Mapping, Optional, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ._errors import NoctHostError
from ._types import (
    Backup,
    Billing,
    OkResponse,
    ProvidersResponse,
    RootPasswordResult,
    Server,
    User,
)
from ._version import __version__

DEFAULT_BASE_URL = "https://api.nocthost.com"
DEFAULT_PUBLIC_BASE_URL = "https://nocthost.com/api/public/v1"

_FAILED_STATUSES = frozenset(["ERROR", "DESTROYING", "DESTROYED"])
_RETRY_GET_STATUSES = frozenset([502, 503, 504])


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _seg(value: str) -> str:
    if not value:
        raise ValueError("id is required")
    return quote(str(value), safe="")


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        n = float(value)
        return n if n >= 0 else None
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, IndexError):
        return None


def _error_message(status: int, body: Any) -> str:
    if isinstance(body, dict):
        for key in ("error", "message"):
            if isinstance(body.get(key), str):
                return cast(str, body[key])
    if isinstance(body, str) and body.strip():
        return body.strip()[:500]
    if status == 401:
        return "Unauthorized: missing, expired or revoked API key"
    if status == 429:
        return "Rate limited"
    return "HTTP {}".format(status)


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _drop_none(d: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


class Servers:
    """Servers: deploy, inspect, power and destroy."""

    def __init__(self, client: "NoctHost") -> None:
        self._client = client

    def list(self) -> List[Server]:
        """GET /servers: all non-destroyed servers."""
        return self._client.request("GET", "/servers")["servers"]

    def get(self, server_id: str) -> Server:
        """GET /servers/{id}"""
        return self._client.request("GET", "/servers/" + _seg(server_id))["server"]

    def create(
        self,
        *,
        label: str,
        plan_id: str,
        region_id: str,
        password: str,
        provider_id: Optional[str] = None,
        os_id: Optional[int] = None,
        app_slug: Optional[str] = None,
        ssh_key: Optional[str] = None,
        billing_cycle: Optional[str] = None,
        expected_monthly_usd: Optional[float] = None,
        **extra: Any,
    ) -> Server:
        """POST /servers: deploy one server. Returns at once with status PROVISIONING.

        ``extra`` keyword arguments are sent as is (camelCase), for fields
        added to the API after this release.
        """
        body = self._create_body(
            label, plan_id, region_id, password, provider_id, os_id, app_slug,
            ssh_key, billing_cycle, expected_monthly_usd, extra,
        )
        return self._client.request("POST", "/servers", body=body)["server"]

    def create_bulk(
        self,
        *,
        count: int,
        label: str,
        plan_id: str,
        region_id: str,
        password: str,
        provider_id: Optional[str] = None,
        os_id: Optional[int] = None,
        app_slug: Optional[str] = None,
        ssh_key: Optional[str] = None,
        billing_cycle: Optional[str] = None,
        expected_monthly_usd: Optional[float] = None,
        **extra: Any,
    ) -> List[Server]:
        """POST /servers/bulk: deploy 1 to 10 identical servers. Labels get -1..-N suffixes."""
        body = self._create_body(
            label, plan_id, region_id, password, provider_id, os_id, app_slug,
            ssh_key, billing_cycle, expected_monthly_usd, extra,
        )
        body["count"] = count
        return self._client.request("POST", "/servers/bulk", body=body)["servers"]

    def destroy(self, server_id: str) -> OkResponse:
        """DELETE /servers/{id}: destroy a server. Billing stops the same hour."""
        return self._client.request("DELETE", "/servers/" + _seg(server_id))

    def providers(self) -> ProvidersResponse:
        """GET /servers/providers: which backends take deploys now and which regions are out of capacity."""
        return self._client.request("GET", "/servers/providers")

    def reboot(self, server_id: str) -> Server:
        """POST /servers/{id}/reboot"""
        return self._power(server_id, "reboot")

    def stop(self, server_id: str) -> Server:
        """POST /servers/{id}/stop. A stopped server is still billed while it exists."""
        return self._power(server_id, "stop")

    def start(self, server_id: str) -> Server:
        """POST /servers/{id}/start"""
        return self._power(server_id, "start")

    def set_root_password(self, server_id: str, password: str) -> RootPasswordResult:
        """POST /servers/{id}/root-password: set a new root password without erasing the disk."""
        return self._client.request(
            "POST", "/servers/{}/root-password".format(_seg(server_id)), body={"password": password}
        )

    def wait_for_active(
        self, server_id: str, timeout: float = 600.0, interval: float = 5.0
    ) -> Server:
        """Poll GET /servers/{id} until the server is ACTIVE and return it.

        Raises NoctHostError with code "server_failed" if the server ends up
        ERROR or is being destroyed, and "wait_timeout" after ``timeout`` seconds.
        """
        deadline = time.monotonic() + timeout
        while True:
            server = self.get(server_id)
            status = server.get("status")
            if status == "ACTIVE":
                return server
            if status in _FAILED_STATUSES:
                raise NoctHostError(
                    "Server {} is {}".format(server_id, status),
                    status=0, code="server_failed", body=server,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NoctHostError(
                    "Server {} not ACTIVE after {}s (last status {})".format(server_id, timeout, status),
                    status=0, code="wait_timeout", body=server,
                )
            _sleep(min(interval, remaining))

    def _power(self, server_id: str, action: str) -> Server:
        return self._client.request("POST", "/servers/{}/{}".format(_seg(server_id), action))["server"]

    @staticmethod
    def _create_body(
        label: str,
        plan_id: str,
        region_id: str,
        password: str,
        provider_id: Optional[str],
        os_id: Optional[int],
        app_slug: Optional[str],
        ssh_key: Optional[str],
        billing_cycle: Optional[str],
        expected_monthly_usd: Optional[float],
        extra: Mapping[str, Any],
    ) -> Dict[str, Any]:
        body = _drop_none(
            {
                "label": label,
                "planId": plan_id,
                "regionId": region_id,
                "password": password,
                "providerId": provider_id,
                "osId": os_id,
                "appSlug": app_slug,
                "sshKey": ssh_key,
                "billingCycle": billing_cycle,
                "expectedMonthlyUsd": expected_monthly_usd,
            }
        )
        body.update(_drop_none(extra))
        return body


class Backups:
    """Backups: full disk snapshots kept for 7 days."""

    def __init__(self, client: "NoctHost") -> None:
        self._client = client

    def list(self, server_id: Optional[str] = None) -> List[Backup]:
        """GET /backups. Pass server_id to list the backups of one server."""
        return self._client.request("GET", "/backups", query={"serverId": server_id})["backups"]

    def create(self, server_id: str) -> Backup:
        """POST /backups: back up a server. Status goes PENDING, then AVAILABLE."""
        return self._client.request("POST", "/backups", body={"serverId": server_id})["backup"]

    def restore(self, backup_id: str, password: str, plan_id: Optional[str] = None) -> Server:
        """POST /backups/{id}/restore: restore onto a new server (new IP)."""
        body = _drop_none({"password": password, "planId": plan_id})
        return self._client.request(
            "POST", "/backups/{}/restore".format(_seg(backup_id)), body=body
        )["server"]

    def delete(self, backup_id: str) -> OkResponse:
        """DELETE /backups/{id}"""
        return self._client.request("DELETE", "/backups/" + _seg(backup_id))


class BillingResource:
    def __init__(self, client: "NoctHost") -> None:
        self._client = client

    def get(self) -> Billing:
        """GET /billing: balance and total hourly spend."""
        return self._client.request("GET", "/billing")


class Public:
    """Public catalog endpoints. No API key needed."""

    def __init__(self, client: "NoctHost") -> None:
        self._client = client

    def deployable(self) -> Dict[str, Any]:
        """GET /api/public/v1/deployable: live summary of clouds, cities, sizes and prices."""
        return self._client.request("GET", "/deployable", auth=False, public_api=True)

    def gpus(
        self,
        gpu_model: Optional[str] = None,
        min_vram_gb: Optional[float] = None,
        continent: Optional[str] = None,
        max_hourly_usd: Optional[float] = None,
        cloud: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /api/public/v1/gpus with optional filters."""
        query = {
            "gpu_model": gpu_model,
            "min_vram_gb": min_vram_gb,
            "continent": continent,
            "max_hourly_usd": max_hourly_usd,
            "cloud": cloud,
        }
        return self._client.request("GET", "/gpus", query=query, auth=False, public_api=True)


class NoctHost:
    """Client for the NoctHost REST API.

    Args:
        api_key: personal API key (nh_...). Falls back to the NOCTHOST_API_KEY
            environment variable. Not needed for ``client.public``.
        base_url: default https://api.nocthost.com
        public_base_url: default https://nocthost.com/api/public/v1
        timeout: per request timeout in seconds, default 60.
        max_retries: automatic retries on 429 (any method) and on 502/503/504
            or network errors (GET only). Default 2.
        headers: extra headers sent with every request.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 2,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NOCTHOST_API_KEY") or None
        self._base_url = base_url.rstrip("/")
        self._public_base_url = public_base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._headers = dict(headers or {})

        self.servers = Servers(self)
        self.backups = Backups(self)
        self.billing = BillingResource(self)
        self.public = Public(self)

    def __repr__(self) -> str:
        return "NoctHost(base_url={!r})".format(self._base_url)

    def me(self) -> User:
        """GET /auth/me: id, email and balance of the key's owner."""
        return self.request("GET", "/auth/me")["user"]

    def wait_for_active(self, server_id: str, timeout: float = 600.0, interval: float = 5.0) -> Server:
        """Shortcut for ``client.servers.wait_for_active``."""
        return self.servers.wait_for_active(server_id, timeout=timeout, interval=interval)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: Optional[Mapping[str, Any]] = None,
        auth: bool = True,
        public_api: bool = False,
    ) -> Any:
        """Low level call. ``path`` is relative to base_url (or public_base_url)."""
        method = method.upper()
        if auth and not self._api_key:
            raise NoctHostError(
                "No API key. Pass one to NoctHost(...) or set NOCTHOST_API_KEY.",
                status=0, code="missing_api_key",
            )

        url = (self._public_base_url if public_api else self._base_url) + path
        if query:
            params = [(k, _query_value(v)) for k, v in query.items() if v is not None and v != ""]
            if params:
                url += "?" + urlencode(params)

        headers = {
            "Accept": "application/json",
            "User-Agent": "nocthost-python/{}".format(__version__),
        }
        headers.update(self._headers)
        if auth:
            headers["Authorization"] = "Bearer {}".format(self._api_key)
        data: Optional[bytes] = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        idempotent = method in ("GET", "HEAD")
        attempt = 0
        while True:
            req = Request(url, data=data, headers=headers, method=method)
            try:
                with urlopen(req, timeout=self._timeout) as resp:
                    return _parse_body(resp.read())
            except HTTPError as err:
                status = err.code
                try:
                    parsed = _parse_body(err.read())
                except Exception:  # pragma: no cover
                    parsed = None
                retry_after = _parse_retry_after(err.headers.get("Retry-After") if err.headers else None)
                retryable = status == 429 or (idempotent and status in _RETRY_GET_STATUSES)
                if retryable and attempt < self._max_retries:
                    _sleep(min(retry_after, 60.0) if retry_after is not None else self._backoff(attempt))
                    attempt += 1
                    continue
                raise NoctHostError(
                    _error_message(status, parsed),
                    status=status, code="http", body=parsed, retry_after=retry_after,
                ) from None
            except (URLError, socket.timeout, TimeoutError, ConnectionError) as err:
                reason = getattr(err, "reason", err)
                timed_out = isinstance(err, (socket.timeout, TimeoutError)) or isinstance(
                    reason, (socket.timeout, TimeoutError)
                )
                if idempotent and attempt < self._max_retries:
                    _sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                if timed_out:
                    raise NoctHostError(
                        "Request timed out after {}s".format(self._timeout), status=0, code="timeout"
                    ) from err
                raise NoctHostError("Network error: {}".format(reason), status=0, code="network") from err

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(0.5 * (2 ** attempt), 8.0)


def _query_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
