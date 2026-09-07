# R2CCoordinationHub extraction plan

## Goal

Move multi-tablet ownership coordination out of `main.py` **without behavior change**. Same WebSocket route, same `R2C_PROTOCOL.md` messages, same tests.

## Current shape (baseline `22254cb`)

| Piece | Location (approx.) |
| --- | --- |
| `R2CCoordinationHub` class | `main.py` ~L1429–4680 (~3252 lines) |
| Models (`R2CZoneState`, owner/confirmation/sighting, …) | nearby in `main.py` |
| WS entry | `@app.websocket("/{designator}/ws/r2c")` → `serve_r2c_websocket` ~L12658+ |
| Tests | `tests/test_r2c_*.py`, `tests/test_coordination_tenant_scope.py` |

## Target layout

```
r2c_coordination/
  __init__.py          # export hub + wire helpers
  models.py            # SQLAlchemy coordination tables
  hub.py               # R2CCoordinationHub
  websocket.py         # serve_r2c_websocket + thin FastAPI route registrar
  constants.py         # lease/heartbeat defaults aligned with R2C_PROTOCOL.md
main.py                # include_router / register_coordination(app, …) only
```

## PR sequence (small diffs)

1. **Add package + move class/models with re-export from `main`** so imports keep working; no route change.
2. **Move websocket handlers** into `websocket.py`; `main` registers them.
3. **Delete shims** once call sites are updated; ensure `Dockerfile` `COPY` includes the new package.
4. **Dockerfile update** — today image copies individual `.py` files; modularization **requires** copying `r2c_coordination/` (and later other packages).

## Exit criteria

- `python -m unittest discover -s tests -p 'test_*.py'` green
- `./qualify_release.sh` green
- `main.py` no longer contains `class R2CCoordinationHub`
- No client or protocol changes

## Non-goals

- Multi-region routing, new message types, MQTT changes, CalTopo on server
