import io
import json
import os
import socket
import unittest
from email.message import Message
from typing import Any, Dict, List, Optional
from unittest import mock
from urllib.error import HTTPError, URLError

import nocthost
from nocthost import NoctHost, NoctHostError

KEY = "nh_test_not_a_real_key"


class _Resp:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.status = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


def _http_error(url: str, status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    return HTTPError(url, status, "err", msg, io.BytesIO(raw))


class FakeUrlopen:
    """Replays queued responses and records every request."""

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, req: Any, timeout: Optional[float] = None) -> Any:
        body = json.loads(req.data.decode()) if req.data else None
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append({"url": req.full_url, "method": req.get_method(), "headers": headers, "body": body, "timeout": timeout})
        if not self.responses:
            raise AssertionError("unexpected request {} {}".format(req.get_method(), req.full_url))
        nxt = self.responses.pop(0)
        if isinstance(nxt, tuple):
            status, payload, hdrs = (nxt + ({},))[:3]
            if status >= 400:
                raise _http_error(req.full_url, status, payload, hdrs)
            return _Resp(payload, status)
        if isinstance(nxt, BaseException):
            raise nxt
        return _Resp(nxt)


def server(**over: Any) -> Dict[str, Any]:
    s = {
        "id": "srv_1",
        "label": "my-bot",
        "status": "PROVISIONING",
        "region": "ams",
        "plan": "vc2-1c-1gb",
        "ipv4": None,
        "hourlyPrice": "0.012",
        "createdAt": "2026-11-18T10:00:00.000Z",
    }
    s.update(over)
    return s


class Base(unittest.TestCase):
    def fake(self, responses: List[Any]) -> FakeUrlopen:
        f = FakeUrlopen(responses)
        p1 = mock.patch("nocthost._client.urlopen", f)
        p2 = mock.patch("nocthost._client._sleep", lambda s: None)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return f


class AccountTests(Base):
    def test_me_sends_bearer_and_unwraps(self) -> None:
        f = self.fake([{"user": {"id": "u1", "email": "a@example.com", "balance": "9.96"}}])
        me = NoctHost(KEY).me()
        self.assertEqual(me["balance"], "9.96")
        c = f.calls[0]
        self.assertEqual(c["url"], "https://api.nocthost.com/auth/me")
        self.assertEqual(c["method"], "GET")
        self.assertEqual(c["headers"]["authorization"], "Bearer " + KEY)
        self.assertEqual(c["headers"]["user-agent"], "nocthost-python/" + nocthost.__version__)
        self.assertEqual(c["timeout"], 60.0)

    def test_billing_and_base_url(self) -> None:
        f = self.fake([{"balance": "9.96", "hourlySpend": "0.012"}])
        b = NoctHost(KEY, base_url="http://localhost:4000/").billing.get()
        self.assertEqual(b, {"balance": "9.96", "hourlySpend": "0.012"})
        self.assertEqual(f.calls[0]["url"], "http://localhost:4000/billing")

    def test_env_fallback(self) -> None:
        f = self.fake([{"servers": []}])
        with mock.patch.dict(os.environ, {"NOCTHOST_API_KEY": KEY}):
            NoctHost().servers.list()
        self.assertEqual(f.calls[0]["headers"]["authorization"], "Bearer " + KEY)

    def test_missing_key(self) -> None:
        f = self.fake([])
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(NoctHostError) as cm:
                NoctHost().servers.list()
        self.assertEqual(cm.exception.code, "missing_api_key")
        self.assertEqual(f.calls, [])


class ServerTests(Base):
    def test_list_get_create_bulk_destroy(self) -> None:
        f = self.fake([
            {"servers": [server()]},
            {"server": server(status="ACTIVE")},
            (202, {"server": server()}),
            (202, {"servers": [server(label="fleet-1"), server(label="fleet-2")]}),
            (202, {"ok": True}),
        ])
        c = NoctHost(KEY)
        self.assertEqual(len(c.servers.list()), 1)
        self.assertEqual(c.servers.get("srv/1")["status"], "ACTIVE")
        self.assertEqual(f.calls[1]["url"], "https://api.nocthost.com/servers/srv%2F1")

        created = c.servers.create(
            label="my-bot", plan_id="vc2-1c-1gb", region_id="ams", provider_id="vultr",
            os_id=1743, password="Example-Passw0rd", ssh_key=None,
        )
        self.assertEqual(created["status"], "PROVISIONING")
        self.assertEqual(f.calls[2]["method"], "POST")
        self.assertEqual(f.calls[2]["url"], "https://api.nocthost.com/servers")
        self.assertEqual(f.calls[2]["body"], {
            "label": "my-bot", "planId": "vc2-1c-1gb", "regionId": "ams",
            "providerId": "vultr", "osId": 1743, "password": "Example-Passw0rd",
        })
        self.assertEqual(f.calls[2]["headers"]["content-type"], "application/json")

        fleet = c.servers.create_bulk(count=2, label="fleet", plan_id="p", region_id="r", password="Example-Passw0rd", autoBackup=True)
        self.assertEqual(len(fleet), 2)
        self.assertEqual(f.calls[3]["url"], "https://api.nocthost.com/servers/bulk")
        self.assertEqual(f.calls[3]["body"]["count"], 2)
        self.assertEqual(f.calls[3]["body"]["autoBackup"], True)

        self.assertEqual(c.servers.destroy("srv_1"), {"ok": True})
        self.assertEqual(f.calls[4]["method"], "DELETE")
        self.assertEqual(f.calls[4]["url"], "https://api.nocthost.com/servers/srv_1")

    def test_power_providers_root_password(self) -> None:
        f = self.fake([
            {"server": server(status="ACTIVE")},
            {"server": server(status="STOPPED")},
            {"server": server(status="ACTIVE")},
            {"providers": [{"id": "vultr", "label": "Vultr", "available": True}], "outages": {}},
            {"password": "Example-Passw0rd", "matchesRequested": True, "note": "x"},
        ])
        c = NoctHost(KEY)
        c.servers.reboot("srv_1")
        self.assertEqual(c.servers.stop("srv_1")["status"], "STOPPED")
        c.servers.start("srv_1")
        self.assertEqual(
            [(x["method"], x["url"]) for x in f.calls[:3]],
            [
                ("POST", "https://api.nocthost.com/servers/srv_1/reboot"),
                ("POST", "https://api.nocthost.com/servers/srv_1/stop"),
                ("POST", "https://api.nocthost.com/servers/srv_1/start"),
            ],
        )
        self.assertEqual(c.servers.providers()["providers"][0]["id"], "vultr")
        self.assertEqual(f.calls[3]["url"], "https://api.nocthost.com/servers/providers")
        r = c.servers.set_root_password("srv_1", "Example-Passw0rd")
        self.assertTrue(r["matchesRequested"])
        self.assertEqual(f.calls[4]["body"], {"password": "Example-Passw0rd"})
        self.assertEqual(f.calls[4]["url"], "https://api.nocthost.com/servers/srv_1/root-password")

    def test_wait_for_active(self) -> None:
        f = self.fake([
            {"server": server()},
            {"server": server()},
            {"server": server(status="ACTIVE", ipv4="203.0.113.10")},
        ])
        s = NoctHost(KEY).wait_for_active("srv_1", timeout=60, interval=0.01)
        self.assertEqual(s["ipv4"], "203.0.113.10")
        self.assertEqual(len(f.calls), 3)

    def test_wait_for_active_failed(self) -> None:
        self.fake([{"server": server(status="ERROR")}])
        with self.assertRaises(NoctHostError) as cm:
            NoctHost(KEY).servers.wait_for_active("srv_1", interval=0.01)
        self.assertEqual(cm.exception.code, "server_failed")
        self.assertEqual(cm.exception.body["status"], "ERROR")

    def test_wait_for_active_timeout(self) -> None:
        self.fake([{"server": server()}] * 3)
        with mock.patch("nocthost._client.time.monotonic", side_effect=[0.0, 0.0, 100.0]):
            with self.assertRaises(NoctHostError) as cm:
                NoctHost(KEY).servers.wait_for_active("srv_1", timeout=10, interval=1)
        self.assertEqual(cm.exception.code, "wait_timeout")


class BackupTests(Base):
    def test_backups(self) -> None:
        b = {"id": "b1", "label": "my-bot", "status": "PENDING", "region": "ams", "plan": "p", "createdAt": "x", "expiresAt": "y"}
        f = self.fake([
            {"backups": [b]},
            {"backups": []},
            (202, {"backup": b}),
            (202, {"server": server()}),
            (202, {"ok": True}),
        ])
        c = NoctHost(KEY)
        self.assertEqual(c.backups.list()[0]["id"], "b1")
        self.assertEqual(f.calls[0]["url"], "https://api.nocthost.com/backups")
        c.backups.list(server_id="srv_1")
        self.assertEqual(f.calls[1]["url"], "https://api.nocthost.com/backups?serverId=srv_1")
        self.assertEqual(c.backups.create("srv_1")["status"], "PENDING")
        self.assertEqual(f.calls[2]["body"], {"serverId": "srv_1"})
        c.backups.restore("b1", password="Example-Passw0rd", plan_id="vc2-2c-4gb")
        self.assertEqual(f.calls[3]["url"], "https://api.nocthost.com/backups/b1/restore")
        self.assertEqual(f.calls[3]["body"], {"password": "Example-Passw0rd", "planId": "vc2-2c-4gb"})
        self.assertEqual(c.backups.delete("b1"), {"ok": True})
        self.assertEqual((f.calls[4]["method"], f.calls[4]["url"]), ("DELETE", "https://api.nocthost.com/backups/b1"))

    def test_restore_without_plan(self) -> None:
        f = self.fake([(202, {"server": server()})])
        NoctHost(KEY).backups.restore("b1", password="Example-Passw0rd")
        self.assertEqual(f.calls[0]["body"], {"password": "Example-Passw0rd"})


class PublicTests(Base):
    def test_public_no_key(self) -> None:
        f = self.fake([
            {"generated_at": "x", "currency": "USD", "categories": []},
            {"generated_at": "x", "currency": "USD", "offer_count": 0, "offers": []},
        ])
        with mock.patch.dict(os.environ, {}, clear=True):
            c = NoctHost()
            c.public.deployable()
            c.public.gpus(gpu_model="L40S", min_vram_gb=48)
        self.assertEqual(f.calls[0]["url"], "https://nocthost.com/api/public/v1/deployable")
        self.assertNotIn("authorization", f.calls[0]["headers"])
        self.assertEqual(f.calls[1]["url"], "https://nocthost.com/api/public/v1/gpus?gpu_model=L40S&min_vram_gb=48")


class ErrorTests(Base):
    def test_401(self) -> None:
        f = self.fake([(401, {"error": "Unauthorized"})])
        with self.assertRaises(NoctHostError) as cm:
            NoctHost(KEY).me()
        e = cm.exception
        self.assertEqual((e.status, e.code, e.message, str(e)), (401, "http", "Unauthorized", "Unauthorized"))
        self.assertEqual(e.body, {"error": "Unauthorized"})
        self.assertEqual(len(f.calls), 1)

    def test_429_retried_then_raised(self) -> None:
        f = self.fake([(429, {"error": "Rate limit exceeded"}, {"Retry-After": "0"}), {"servers": []}])
        self.assertEqual(NoctHost(KEY).servers.list(), [])
        self.assertEqual(len(f.calls), 2)

        self.fake([(429, "slow down", {"Retry-After": "3"})])
        with self.assertRaises(NoctHostError) as cm:
            NoctHost(KEY, max_retries=0).servers.list()
        self.assertEqual((cm.exception.status, cm.exception.retry_after, cm.exception.message), (429, 3.0, "slow down"))

    def test_503_retry_only_for_get(self) -> None:
        f = self.fake([(503, {"error": "Busy, retry"}, {"Retry-After": "0"})])
        with self.assertRaises(NoctHostError) as cm:
            NoctHost(KEY).servers.create(label="a", plan_id="p", region_id="r", password="Example-Passw0rd")
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(len(f.calls), 1)

        self.fake([(503, {"error": "Busy, retry"}), {"balance": "1", "hourlySpend": "0"}])
        self.assertEqual(NoctHost(KEY).billing.get()["balance"], "1")

    def test_network_and_timeout(self) -> None:
        self.fake([URLError("connection refused")])
        with self.assertRaises(NoctHostError) as cm:
            NoctHost(KEY).backups.create("srv_1")
        self.assertEqual((cm.exception.status, cm.exception.code), (0, "network"))

        self.fake([socket.timeout("timed out")])
        with self.assertRaises(NoctHostError) as cm:
            NoctHost(KEY, max_retries=0).servers.list()
        self.assertEqual(cm.exception.code, "timeout")


if __name__ == "__main__":
    unittest.main()
