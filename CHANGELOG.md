# Changelog

## 0.1.0 (2026-11-18)

First release.

- `NoctHost` client with API key auth (`nh_...` or `NOCTHOST_API_KEY`)
- Account: `me()`, `billing.get()`
- Servers: `list`, `get`, `create`, `create_bulk`, `destroy`, `providers`, `reboot`, `stop`, `start`, `set_root_password`
- `wait_for_active(id, timeout, interval)` polling helper
- Backups: `list`, `create`, `restore`, `delete`
- Public catalog without a key: `public.deployable()`, `public.gpus(...)`
- `NoctHostError` with `status`, `code`, `body`, `retry_after`
- Automatic retries on 429 (all methods) and on 502/503/504 or network errors (GET only)
- Standard library only, typed (`py.typed`), Python 3.9+
