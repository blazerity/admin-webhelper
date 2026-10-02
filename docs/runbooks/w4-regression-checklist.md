# W4 regression checklist

## Авто

- [ ] `python3 -m pytest -q` зелёный
- [ ] `/api/v1/health`, `/api/v1/network/summary` (login)
- [ ] Старые JSON (`/api/network/summary`, `/map/status`) живы

## UI / ops

1. Карта без CDN: Bootstrap collapse секторов, modal bulk script
2. CSV: `/accounts/export.csv`, `/actions/export.csv`, `/admin/poll-runs/export.csv`
3. Runbooks открываются из `docs/runbooks/`
4. Smoke W1–W3: login, карта, bulk, notifications bell, roles

## Air-gap

См. [offline-assets.md](offline-assets.md).
