# nocthost

Official Python client for the [NoctHost](https://nocthost.com) API.
Deploy and manage hourly-billed cloud servers on major clouds such as Vultr, Hetzner,
Linode and OVHcloud from one account and one prepaid balance.

- Zero dependencies, standard library only (`urllib`)
- Python 3.9 or newer
- Fully typed (`py.typed`)

Full API reference: https://nocthost.com/docs/api

## Install

```sh
pip install nocthost
```

## Quick start

Create a personal API key in the dashboard under Profile, API keys. It starts with `nh_`.
Keep it out of source control; the client also reads it from the `NOCTHOST_API_KEY`
environment variable.

```python
import os
from nocthost import NoctHost

client = NoctHost(os.environ["NOCTHOST_API_KEY"])

billing = client.billing.get()
print("Balance ${balance}, spending ${hourlySpend}/h".format(**billing))

server = client.servers.create(
    label="my-bot",
    plan_id="vc2-1c-1gb",
    region_id="ams",
    provider_id="vultr",
    password=os.environ["ROOT_PASSWORD"],
)

ready = client.wait_for_active(server["id"], timeout=600)
print("ssh root@" + ready["ipv4"])
```

## Configuration

```python
client = NoctHost(
    "nh_...",                                   # default: NOCTHOST_API_KEY
    base_url="https://api.nocthost.com",
    public_base_url="https://nocthost.com/api/public/v1",
    timeout=60.0,                               # seconds, per request
    max_retries=2,                              # 429 on any method; 502/503/504 and network errors on GET only
    headers={"X-Trace": "1"},                   # extra headers on every request
)
```

## Methods

Responses are plain `dict`s with the API's own keys (camelCase), described by the
TypedDicts exported from the package (`Server`, `Backup`, `Billing`, ...). Money and
prices are decimal strings in USD (`"9.96"`). Methods unwrap the response envelope:
`servers.list()` returns the list, not `{"servers": [...]}`.

### Account

| Method | HTTP | Returns |
| --- | --- | --- |
| `client.me()` | `GET /auth/me` | `User`: `id`, `email`, `balance` |
| `client.billing.get()` | `GET /billing` | `Billing`: `balance`, `hourlySpend` |

### Servers

| Method | HTTP | Returns |
| --- | --- | --- |
| `client.servers.list()` | `GET /servers` | `list[Server]` (all non-destroyed) |
| `client.servers.get(id)` | `GET /servers/{id}` | `Server` |
| `client.servers.create(...)` | `POST /servers` | `Server` with status `PROVISIONING` |
| `client.servers.create_bulk(count=..., ...)` | `POST /servers/bulk` | `list[Server]`, up to 10 |
| `client.servers.destroy(id)` | `DELETE /servers/{id}` | `{"ok": True}` |
| `client.servers.providers()` | `GET /servers/providers` | `{"providers": [...], "outages": {...}}` |
| `client.servers.reboot(id)` | `POST /servers/{id}/reboot` | `Server` |
| `client.servers.stop(id)` | `POST /servers/{id}/stop` | `Server` |
| `client.servers.start(id)` | `POST /servers/{id}/start` | `Server` |
| `client.servers.set_root_password(id, password)` | `POST /servers/{id}/root-password` | `{"password", "matchesRequested", "note"}` |
| `client.servers.wait_for_active(id, timeout=600, interval=5)` | polls `GET /servers/{id}` | `Server` once `ACTIVE` |

`client.wait_for_active(...)` is the same helper on the client itself.

`create` takes keyword arguments:

| Argument | API field | Notes |
| --- | --- | --- |
| `label` | `label` | Latin letters, digits, dot, dash, underscore |
| `plan_id` | `planId` | |
| `region_id` | `regionId` | |
| `password` | `password` | 12-72 Latin chars, 3 of lower/upper/digits/symbols, no spaces |
| `provider_id` | `providerId` | optional, see `servers.providers()` |
| `os_id` | `osId` | optional, default Ubuntu 22.04; or `app_slug`, not both |
| `app_slug` | `appSlug` | optional one-click app (Vultr) |
| `ssh_key` | `sshKey` | optional public key for root |
| `billing_cycle` | `billingCycle` | optional, `"hourly"` (default) or `"monthly"` |
| `expected_monthly_usd` | `expectedMonthlyUsd` | required with monthly on regular sizes |

Any other keyword argument is sent as is, so fields added to the API later work without
an upgrade. `create_bulk` takes the same arguments plus `count` (1 to 10). Labels get
`-1..-N` suffixes.

Deploys are asynchronous. Check `servers.providers()` before picking a provider and
region, then poll with `wait_for_active`. It raises `NoctHostError` with code
`server_failed` if the server lands in `ERROR` or is being destroyed, or `wait_timeout`
after `timeout` seconds.

Server statuses: `PROVISIONING`, `ACTIVE`, `STOPPED`, `SUSPENDED`, `ARCHIVED`,
`DESTROYING`, `DESTROYED`, `ERROR`. A stopped server is still billed while it exists.

### Backups

Full disk snapshots, kept for 7 days.

| Method | HTTP | Returns |
| --- | --- | --- |
| `client.backups.list(server_id=None)` | `GET /backups` | `list[Backup]` |
| `client.backups.create(server_id)` | `POST /backups` | `Backup` with status `PENDING` |
| `client.backups.restore(id, password=..., plan_id=None)` | `POST /backups/{id}/restore` | new `Server` (new IP) |
| `client.backups.delete(id)` | `DELETE /backups/{id}` | `{"ok": True}` |

### Public catalog (no API key)

| Method | HTTP | Returns |
| --- | --- | --- |
| `client.public.deployable()` | `GET https://nocthost.com/api/public/v1/deployable` | clouds, cities, sizes and prices available now |
| `client.public.gpus(...)` | `GET https://nocthost.com/api/public/v1/gpus` | GPU offers |

`gpus` filters: `gpu_model`, `min_vram_gb`, `continent`, `max_hourly_usd`, `cloud`.

```python
offers = NoctHost().public.gpus(gpu_model="L40S", continent="Europe")["offers"]
```

### Low level

`client.request(method, path, body=None, query=None)` calls any other endpoint from the
[API reference](https://nocthost.com/docs/api) with the same auth, retries and errors.

## Errors

Every failure raises `NoctHostError`:

```python
from nocthost import NoctHost, NoctHostError

try:
    client.servers.get("does-not-exist")
except NoctHostError as err:
    print(err.status)       # HTTP status, 0 if no response
    print(err.code)         # "http", "network", "timeout", "wait_timeout", "server_failed", "missing_api_key"
    print(err.message)      # the API's error text when it sent one
    print(err.body)         # parsed error body
    print(err.retry_after)  # seconds, from Retry-After
```

- `401`: missing, expired or revoked key
- `429`: rate limited (300 requests per minute per key). The client retries with backoff
  before raising.

## Development

```sh
python3 -m unittest
```

## Links

- Website: https://nocthost.com
- API docs: https://nocthost.com/docs/api
- OpenAPI spec: https://nocthost.com/openapi.json

## License

MIT, see [LICENSE](./LICENSE).
