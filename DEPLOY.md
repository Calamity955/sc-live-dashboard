# SC Live Dashboard — Deploy produzione

> Containerizzato con Docker, esposto solo via Tailscale, restart automatico.

## Architettura

- **Container:** `sc-dashboard` (image `sc-dashboard:latest`)
- **Porta:** `8765`, bind **esclusivo** su IP Tailscale `100.98.10.55`
- **Restart policy:** `unless-stopped` (riparte dopo reboot, non se fermato manualmente)
- **Healthcheck:** `GET /api/status` ogni 30s
- **Volume persistente:** `./data` (SQLite events_log + CSV correnti)
- **Logs:** rotazione automatica, max 30MB (3 file × 10MB)

## URL accesso

- Solo via Tailscale: **http://100.98.10.55:8765**
- DNS Tailscale: **http://antonio-pascarella-home.tailc45906.ts.net:8765**

> ⚠️ Non esposto su IP pubblico né su localhost: solo dispositivi Tailscale autenticati.

## Comandi operativi

```bash
cd /home/antonio-pascarella/projects/sc-dashboard

# Avviare
docker compose up -d

# Fermare
docker compose down

# Riavviare
docker compose restart

# Vedere log live
docker logs -f sc-dashboard

# Vedere stato
docker ps --filter "name=sc-dashboard"

# Rebuild (dopo modifiche al codice)
docker compose build
docker compose up -d
```

## Configurazione

Tutta in `.env.prod` (NON versionare).

### Sorgente dati

```env
# FASE 1 — fallback locale (current.csv copiato a mano in data/csv/)
DATA_SOURCE=local
DATA_PATH=./data/csv

# FASE 2 — quando IT attiva SFTP
DATA_SOURCE=sftp
SFTP_HOST=10.x.x.x
SFTP_PORT=22
SFTP_USER=sc_dashboard
SFTP_PASSWORD=...        # oppure SFTP_KEY_PATH=/app/keys/id_ed25519
SFTP_PATH=/consegne_oggi.csv     # path file sul server IT
SFTP_MODE=single                 # oppure "newest" per pescare l'ultimo file in cartella
SFTP_PATTERN=*.csv*              # solo per mode=newest
SFTP_KNOWN_HOSTS=                # vuoto = no verifica host (LAN); compilare per strict
```

Lo switch è caldo (con riavvio):
```bash
# modifica .env.prod
docker compose up -d  # ricarica env
```

### Filtro data

- `FILTER_TODAY=1` → scarta righe con Data Consegna ≠ oggi (Europe/Rome)
- `FILTER_TODAY=0` → carica tutto (utile per debug)

### Poller

- `POLL_INTERVAL=120` → 2 minuti (allineato a frequenza IT)
- Modificare se serve.

## Restart automatico

Verificato con policy `unless-stopped`. Comportamento:

| Evento | Azione |
|---|---|
| Crash applicativo (exit non-zero) | Restart automatico |
| Reboot server | Restart automatico al boot di Docker |
| `docker stop sc-dashboard` (manuale) | NO restart finché non `docker start` |
| `docker compose down` | NO restart finché `up -d` |

Allineato a tutti gli altri tuoi container (fatturazione-sc, magazzino, ecc.).

## Migrazione da fase test a produzione FTP

Quando l'IT comunica le credenziali SFTP:

1. Modifica `.env.prod` come da sezione "Sorgente dati"
2. Rimuovi il `current.csv` dummy in `./data/csv/` se vuoi (non più letto)
3. `docker compose up -d`
4. Verifica log: `docker logs -f sc-dashboard` → cerca `Source=SftpSource`
5. Verifica primo download su Tailscale URL → `/api/status`

In caso di problemi, switch indietro in 10 secondi:
```bash
# modifica DATA_SOURCE=local in .env.prod
docker compose up -d
```

## Backup

Tutti i dati persistenti vivono in `./data/`:
- `sc.db` — SQLite con `events_log` (storia eventi)
- `csv/current.csv` — ultimo snapshot ricevuto (utile per debug)

Backup giornaliero consigliato:
```bash
tar -czf "backup-$(date +%Y%m%d).tar.gz" data/
```

## Sicurezza

- ✅ Bind esclusivo su IP Tailscale (no esposizione internet)
- ✅ Solo dispositivi Tailscale autenticati possono raggiungere la dashboard
- ✅ Solo lettura su SFTP (no scrittura)
- ✅ Container non-privileged
- ✅ Credenziali in `.env.prod` (non versionato)

## Risorse

Misurate durante test:
- RAM: ~80-100 MB
- CPU: < 1% a riposo, brevi picchi al polling
- Disco: ~50 MB image + cresce ~5 MB/mese (events_log)

## Pulizia events_log (futura)

Tabella `events_log` cresce indefinitamente. Quando avrà 1+ GB, aggiungere cron:

```sql
DELETE FROM events_log WHERE ts < datetime('now', '-90 days');
VACUUM;
```
