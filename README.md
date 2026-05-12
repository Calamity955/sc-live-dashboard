# SC Live Dashboard

**Real-time operations dashboard for last-mile delivery monitoring.**

Built for **Servizi Campania Srl**, a Campania-based logistics company managing last-mile deliveries via the TransitPoint platform.

The dashboard ingests CSV exports of the daily delivery state, compares snapshots, and pushes live updates with audio feedback to an office TV (kiosk mode). Designed to extend toward a multi-screen "Mission Control" setup for the traffic office.

---

## Features

- **Live KPI tiles** by delivery state (Scheduled / In progress / Completed / Failed / Refused) with custom color palette
- **Per-chain cards** (active customers/partners for the day) with worst-first sorting
- **Per-vehicle cards** with adaptive layout (Comfort/Compact/Dense based on vehicle count)
- **Punctuality KPI**: real-time compliance percentage with configurable thresholds (default: +10 min tolerance, slow > 40 min)
- **Live event timeline** via WebSocket
- **Audio feedback** (Web Audio API, generated in-browser — no external assets)
  - Ping on completed delivery
  - Buzz on failure
  - Double-tap on refused
  - Combined alert on severe lateness
  - Alert on connection loss
- **Drill-down modals** for vehicle → deliveries → single delivery details
- **Strategy-pattern data source**: swap between local folder, SFTP, or (future) SOAP without code changes
- **Persistent event log** in SQLite for historical analysis

## Tech stack

- **Backend:** Python 3.12 + FastAPI + asyncio + SQLite (WAL) + WebSocket
- **SFTP client:** asyncssh (with gzip support)
- **Frontend:** Single-page HTML + Alpine.js + Tailwind CDN + Chart.js (no build step)
- **Audio:** Web Audio API oscillators (no external files)
- **Deploy:** Docker + docker-compose, Tailscale-only binding

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       BROWSER (kiosk + desktop)                │
│   Dashboard SPA (HTML/JS) — WebSocket + REST + Audio API       │
└──────────────▲────────────────────────────────────▲────────────┘
               │ WS (push events)                   │ REST (drill)
┌──────────────┴────────────────────────────────────┴────────────┐
│                     BACKEND (FastAPI + asyncio)                │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Poller      │→ │ Diff engine  │→ │ Event bus (WS push)  │   │
│  │ (CSV/SFTP)  │  │ (state diff) │  │                      │   │
│  └─────────────┘  └──────┬───────┘  └──────────────────────┘   │
│                          ▼                                     │
│                  ┌──────────────────┐                          │
│                  │ SQLite           │  ← snapshot + events_log │
│                  └──────────────────┘                          │
└────────────────────────────────────────────────────────────────┘
                          │
                          ▼
                ┌──────────────────┐
                │  Data source     │
                │  (local | SFTP)  │
                └──────────────────┘
```

### Data flow

1. **Poller** reads the latest CSV from the configured source every `POLL_INTERVAL` seconds
2. **Parser** filters rows by `Tipo Riga = 1` and optionally by today's date
3. **Diff engine** compares with the previous snapshot per CID, emits typed events
4. **WebSocket manager** broadcasts events to all connected clients
5. **SQLite** persists events for replay and historical queries

---

## Quick start

### Docker (production)

```bash
git clone https://github.com/Calamity955/sc-live-dashboard.git
cd sc-live-dashboard

# Copy the env template and fill in your config
cp .env.prod.example .env.prod
# edit .env.prod with your values

docker compose up -d
docker logs -f sc-dashboard
```

Open http://localhost:8765 (or your bound IP).

### Dev mode (local CSV replay)

```bash
git clone https://github.com/Calamity955/sc-live-dashboard.git
cd sc-live-dashboard

pip install -r requirements.txt

# Generate mock scenario from sample CSV
python scripts/generate_mock_scenario.py

# Start backend + replay loop
./scripts/start_dev.sh
```

---

## Configuration

Configuration is environment-based, in `.env.prod`:

```env
# --- Data source ---
DATA_SOURCE=local           # local | sftp
DATA_PATH=./data/csv

# --- SFTP (when production source is ready) ---
SFTP_HOST=
SFTP_PORT=22
SFTP_USER=
SFTP_PASSWORD=
SFTP_KEY_PATH=
SFTP_PATH=/consegne_oggi.csv
SFTP_MODE=single            # single | newest
SFTP_PATTERN=*.csv*
SFTP_KNOWN_HOSTS=

# --- Poller ---
POLL_INTERVAL=120           # seconds

# --- Filters ---
FILTER_TODAY=1              # drop rows with delivery date != today

# --- Storage ---
DB_PATH=/app/data/sc.db

# --- HTTP ---
HOST=0.0.0.0
PORT=8765
```

### Switching from local to SFTP

```bash
# 1. Edit .env.prod
sed -i 's/^DATA_SOURCE=local/DATA_SOURCE=sftp/' .env.prod
# Fill SFTP_HOST, SFTP_USER, SFTP_PASSWORD, SFTP_PATH

# 2. Apply
docker compose up -d
```

The backend hot-detects the new config and switches source. No code changes needed.

---

## CSV format

The dashboard expects TransitPoint's standard CSV export:

- **Encoding:** UTF-8
- **Separator:** comma
- **Quoting:** double quotes
- **Filter:** only rows with `Tipo Riga = 1` are processed
- **Frequency:** snapshot of the day, updated every ~2 minutes
- **Gzip:** `.csv.gz` files are automatically decompressed (recommended for large datasets)

### Key columns

The parser reads ~25 essential columns and preserves all others in `raw_extra` for future modules:

- `CID` (unique technical key)
- `Riferimento` (logical key, multipart with `!1`, `!2`, ... suffixes for multi-package deliveries)
- `Catena`, `Punto Vendita`
- `Stato`, `Furgone`, `Data Consegna`, `Ora Da`, `Ora A`, `Data Cons. Reale`
- `Cognome`, `Nome`, `Città`, `Provincia`
- `Tipo Prodotto`, `Marca`, `Modello`, `Colli`
- `Causale`, `Note Trasportatore`
- `Sorgente Consegna`, `Proprieta Furgone`

---

## States & colors

The dashboard preserves TransitPoint's state names literally:

| State | Color | Hex |
|---|---|---|
| Programmata | Fuchsia | `#e91e63` |
| Lavorazione | Light green | `#a5d6a7` |
| Evasa | Dark green | `#2e7d32` |
| Fallita | Red | `#d32f2f` |
| Respinta | Yellow | `#fbc02d` |
| Sospesa | Gray | `#9e9e9e` |
| Cancellata | Off-white | `#f5f5f5` |

---

## API reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Frontend SPA |
| `/api/status` | GET | Health + poller info |
| `/api/today` | GET | Current snapshot: KPI + chains + vehicles |
| `/api/events?since=N` | GET | Events since timestamp |
| `/api/events/recent?limit=20` | GET | Last N events |
| `/api/delivery/{cid}` | GET | Single delivery + related packages |
| `/ws/events` | WS | Live event push |

### Event types

- `delivery.added`
- `delivery.status_changed` (includes `old_stato`, `new_stato`, optional `puntualita`)
- `delivery.removed`

---

## Project structure

```
sc-live-dashboard/
├── ARCHITETTURA.md          # Design doc (Italian)
├── DEPLOY.md                # Production ops guide
├── SESSION_HANDOFF.md       # AI-assisted continuation guide
├── Dockerfile
├── docker-compose.yml
├── .env.prod.example        # Config template
├── requirements.txt
├── backend/
│   ├── main.py              # FastAPI app
│   ├── parser.py            # CSV parser
│   ├── diff_engine.py       # Snapshot diff + punctuality flag
│   ├── source.py            # Strategy: LocalFolder / SFTP
│   ├── models.py            # Pydantic models
│   ├── db.py                # SQLite layer
│   └── ws.py                # WebSocket manager
├── frontend/
│   └── index.html           # SPA (Alpine.js + Tailwind + Chart.js)
└── scripts/
    ├── generate_mock_scenario.py
    ├── replay_mocks.py
    └── start_dev.sh
```

---

## Security

- Production deployments bind the listener to a single Tailscale IP — never to `0.0.0.0` or a public interface
- Credentials live in `.env.prod` (gitignored)
- The dashboard is **read-only** on the data source — no writes to TransitPoint
- Container runs as non-privileged user

---

## Roadmap

- [x] CSV parsing + diff engine
- [x] Adaptive layout (Comfort/Compact/Dense)
- [x] Per-chain and per-vehicle aggregation
- [x] Punctuality KPI
- [x] Audio feedback (Web Audio API)
- [x] Docker production deploy
- [x] SFTP data source
- [ ] Weekly/monthly views with Chart.js
- [ ] Dedicated punctuality view with breakdown + PDF export
- [ ] "Mission Control" extension for traffic office
- [ ] Live revenue valuation module
- [ ] WEEE/RAEE tracking module

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

Built for Servizi Campania Srl by **Atlas** (AI operations assistant) in collaboration with **Antonio Pascarella** (Calamity D. 夕暉).

---

*"This dashboard does one job: tell you what's happening with your deliveries, right now, with no friction."*
