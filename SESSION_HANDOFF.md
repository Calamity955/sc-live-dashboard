# SC Live Dashboard — Session Handoff

> **Scopo:** questo documento è scritto per Atlas (o qualsiasi assistente AI) che riprenda il progetto in una sessione futura. Contiene tutto il contesto necessario per continuare senza dover rileggere chat o ricostruire decisioni.
>
> **Ultimo aggiornamento:** 2026-05-12 — fine prima sessione di build.

---

## 1. Stato attuale (snapshot 2026-05-12)

✅ **Dashboard real-time funzionante in produzione**, containerizzata, accessibile via Tailscale.

- Container Docker `sc-dashboard` running sul VPS di casa (`antonio-pascarella-home`)
- Porta `8765` bindata **solo** su IP Tailscale `100.98.10.55`
- Restart automatico (`unless-stopped`)
- Sorgente dati attuale: **CSV locale di test** (samples/tp_20260512_sample.csv)
- Pronta a switchare a **SFTP** appena IT fornirà credenziali

**URL accesso (solo via Tailscale):**
- http://100.98.10.55:8765
- http://antonio-pascarella-home.tailc45906.ts.net:8765

---

## 2. Cosa fare alla prossima sessione (azioni concrete)

### 🎯 Azione principale: switch a SFTP produzione

Quando Calamity dirà "ho le credenziali SFTP", procedi così:

1. **Chiedi a Calamity i seguenti dati** (in chat sicura):
   - Host SFTP (IP o hostname)
   - Porta (default 22)
   - Username
   - Password OPPURE chiave SSH (path o contenuto)
   - Path file remoto (es. `/consegne_oggi.csv` o cartella se mode=newest)
   - Eventuale fingerprint del server (per known_hosts strict)

2. **Modifica `/home/antonio-pascarella/projects/sc-dashboard/.env.prod`** decommentando e compilando il blocco SFTP:
   ```env
   DATA_SOURCE=sftp
   SFTP_HOST=...
   SFTP_PORT=22
   SFTP_USER=...
   SFTP_PASSWORD=...
   # SFTP_KEY_PATH=/app/keys/id_ed25519   # alternativa a password
   SFTP_PATH=/consegne_oggi.csv
   SFTP_MODE=single                       # oppure "newest"
   # SFTP_PATTERN=*.csv*                  # solo per mode=newest
   SFTP_KNOWN_HOSTS=                      # vuoto = no verifica (LAN)
   ```

3. **Se Calamity ha fornito una chiave SSH:**
   ```bash
   mkdir -p /home/antonio-pascarella/projects/sc-dashboard/keys
   # Scrivi la chiave in keys/sftp_key
   chmod 600 /home/antonio-pascarella/projects/sc-dashboard/keys/sftp_key
   ```
   E aggiungi al `docker-compose.yml` il volume mount:
   ```yaml
   volumes:
     - ./data:/app/data
     - ./keys:/app/keys:ro
   ```
   Imposta in `.env.prod`: `SFTP_KEY_PATH=/app/keys/sftp_key`

4. **Test connessione SFTP** (senza riavviare il container) per validare credenziali:
   ```bash
   cd /home/antonio-pascarella/projects/sc-dashboard
   docker exec sc-dashboard python -c "
   import asyncio, os, asyncssh
   async def test():
       async with await asyncssh.connect(
           host=os.environ['SFTP_HOST'],
           port=int(os.getenv('SFTP_PORT', 22)),
           username=os.environ['SFTP_USER'],
           password=os.environ.get('SFTP_PASSWORD'),
           known_hosts=None,
       ) as conn:
           async with await conn.start_sftp_client() as sftp:
               entries = await sftp.listdir('.')
               print('Connesso. Files in home:', entries[:10])
   asyncio.run(test())
   "
   ```
   ⚠️ Se le credenziali sono giuste, vedrai la lista file. Se errore, mostra l'errore a Calamity.

5. **Applica config + riavvia:**
   ```bash
   cd /home/antonio-pascarella/projects/sc-dashboard
   docker compose up -d
   ```

6. **Verifica funzionamento:**
   ```bash
   docker logs -f sc-dashboard
   ```
   Devi vedere:
   - `Source=SftpSource` nel log di startup
   - `Snapshot reloaded: NNN deliveries` ogni 2 minuti
   - Nessun `SFTP load error` ripetuto

7. **Conferma a Calamity:**
   - URL Tailscale ancora funzionante
   - Eventi che arrivano via WebSocket
   - Audio funziona
   - Numero consegne plausibile per giornata corrente

### 🔄 Rollback rapido (se qualcosa va male)

```bash
cd /home/antonio-pascarella/projects/sc-dashboard
sed -i 's/^DATA_SOURCE=sftp/DATA_SOURCE=local/' .env.prod
docker compose up -d
```
In 10 secondi torni al CSV locale (test).

---

## 3. Contesto del progetto (per chi non ha letto la chat)

### Cliente / Obiettivo
**Calamity D. 夕暉 / Antonio Pascarella** — gestisce **Servizi Campania Srl**, azienda di logistica/consegne ultimo miglio in Campania. Vuole una dashboard real-time da appendere a una TV in ufficio per monitorare le consegne TransitPoint in corso, con:
- Tile KPI per stato (Programmate / Evase / Fallite / Respinte / Puntualità)
- Card per catena (committenti)
- Card per targa/furgone (squadre)
- Audio feedback su eventi (ping/buzz/double-tap)
- Drill-down su consegna singola
- Estensione futura ("Mission Control") con schermo gemello in ufficio traffico

### Sorgente dati
- **TransitPoint** (gestionale logistico) esporta CSV completo della giornata via SFTP ogni 2 minuti
- L'IT di Calamity prepara lo spazio SFTP (al momento del handoff: in attesa)
- CSV con 130 colonne, encoding UTF-8, separatore `,`, quoting `"`
- File ruotato dall'IT (cancellati quelli più vecchi di 24-48h)

### Pattern architetturale
- **Input layer astratto** (strategy): `LocalFolderSource` per test, `SftpSource` per prod, futuro `WsdSource` (SOAP) se mai disponibile
- **Diff engine** confronta snapshot vecchio vs nuovo per CID → genera eventi tipizzati
- **WebSocket** push eventi al frontend in tempo reale
- **SQLite** per persistenza events_log
- **Frontend SPA** Alpine.js + Tailwind CDN + Web Audio API (no build step, no dipendenze esterne)

---

## 4. Decisioni operative cristallizzate

Queste sono **definite** e non vanno rinegoziate.

### Filtri e chiavi
| Cosa | Valore |
|---|---|
| Filtro righe CSV | **Solo `Tipo Riga = 1`** (esclude prelievi negozio/magazzino) |
| Chiave tecnica diff (interno) | `CID` (numerico, univoco) |
| Chiave logica UI (consegna fisica) | `(Catena, Riferimento_base)` dove `Riferimento_base = Riferimento` senza suffisso `!N` |
| Conteggio principale | **Consegne fisiche** (non colli) |
| Display | `"X consegne (Y colli)"` |
| Filtro data | `FILTER_TODAY=1` lato backend (anche se IT filtra già) |

### Stati TP (mostrare letteralmente)
`Programmata`, `Lavorazione`, `Evasa`, `Fallita`, `Respinta`, `Sospesa`, `Cancellata`

Nessuna normalizzazione/rinomina.

### Palette colori (decisa da Calamity)
| Stato | Colore | Hex |
|---|---|---|
| Programmata | Fucsia | `#e91e63` |
| Lavorazione | Verde chiaro | `#a5d6a7` |
| Evasa | Verde scuro | `#2e7d32` |
| Fallita | Rosso | `#d32f2f` |
| Respinta | Giallo | `#fbc02d` |
| Sospesa | Grigio | `#9e9e9e` |
| Cancellata | Bianco/grigio chiarissimo | `#f5f5f5` (testo `#666`) |

### Puntualità (parametri concordati)
- Tolleranza in fascia: **+10 min** dopo `Ora A`
- Ritardo lieve: fino a `+40 min` totali (10 toll + 30)
- Ritardo grave: oltre `+40 min`
- Flag: `in_fascia` / `ritardo_lieve` / `ritardo_grave` / `anticipata`
- Tutte le consegne hanno fascia (niente caso N/A)

### Audio
- **Web Audio API** in-browser (oscillatori, **niente file .wav esterni**)
- Toggle ON/OFF nel header
- Eventi:
  - `delivery.completed` (Evasa) → ping 880 Hz, sine, 150ms, vol 0.3
  - `delivery.failed` (Fallita) → buzz 220 Hz, square, 300ms, vol 0.4
  - `delivery.refused` (Respinta) → double tap 660 Hz, 80ms × 2, vol 0.35
  - `delivery.late` (Evasa + ritardo grave) → combo ping + buzz 440 Hz
  - `connection.lost` (WS down > 5s) → alert ripetuto 3 toni
- Debounce: max 1 ping/3s, burst >5 in 10s → audio aggregato

### Layout adattivo
Numero targhe → modalità:
- 1-12 → **Comfort** (card 280×180, 3-4 col)
- 13-20 → **Compact** (200×140, 5 col)
- 21-30+ → **Dense** (150×100, 6-7 col)

### Ordinamento card (default)
Worst-first: fallite > respinte > programmate aperte > tutto ok

### Frequenze
- Export IT → SFTP: **ogni 2 minuti** (accordato)
- Poll backend → SFTP: `POLL_INTERVAL=120` (allineato)
- Reload frontend snapshot: triggerato da WebSocket `snapshot_refreshed`

### Restrizioni di sicurezza
- ✅ **Bind esclusivo su IP Tailscale** `100.98.10.55:8765` (no localhost, no IP pubblico)
- ✅ Solo dispositivi Tailscale autenticati possono accedere
- ✅ Read-only sul SFTP (dashboard non scrive mai)
- ✅ Credenziali in `.env.prod`, non versionato
- ✅ Container non-privileged

---

## 5. Struttura progetto

```
/home/antonio-pascarella/projects/sc-dashboard/
├── ARCHITETTURA.md         # documento design completo v0.5
├── DEPLOY.md               # comandi operativi prod
├── SESSION_HANDOFF.md      # questo file
├── README.md               # entry-point dev
├── Dockerfile              # image produzione
├── docker-compose.yml      # bind Tailscale + restart + volumi
├── .env.prod               # config prod (NOT versionato)
├── .dockerignore
├── requirements.txt
├── backend/
│   ├── main.py             # FastAPI app + poller + endpoint REST/WS
│   ├── parser.py           # CSV → Delivery (filtro Tipo Riga=1, FILTER_TODAY)
│   ├── diff_engine.py      # diff snapshot + flag puntualità
│   ├── source.py           # Strategy: LocalFolderSource, SftpSource
│   ├── models.py           # Pydantic Delivery + Event
│   ├── db.py               # SQLite events_log + current
│   └── ws.py               # WebSocket manager
├── frontend/
│   └── index.html          # SPA Alpine.js + Tailwind + Chart.js (no build)
├── samples/
│   └── tp_20260512_sample.csv  # CSV reale di esempio (130 col, 408 righe)
├── scripts/
│   ├── generate_mock_scenario.py  # genera 6 snapshot per demo
│   ├── replay_mocks.py            # loop replay (solo dev)
│   └── start_dev.sh               # avvio modalità sviluppo
└── data/                   # persistente, volume Docker
    ├── sc.db               # SQLite events_log
    └── csv/
        └── current.csv     # ultimo snapshot ricevuto
```

---

## 6. CSV di esempio analizzato

`samples/tp_20260512_sample.csv` — export reale del 12/05/2026.

- **408 righe totali** (CID univoci)
- **130 colonne**
- **359 righe `Tipo Riga = 1`** (le altre 49 = prelievi automatici, esclusi)
- **168 consegne fisiche uniche** (per `Catena + Riferimento base`)
- **14 targhe attive** (DM807GK 69 colli, GV194TG 41, ecc.)
- **13 catene** (Deghi Spa dominante con 192 colli, poi Arcobaleno 68, Amzn 33, MediaWorld 28)
- **Stati osservati:** Programmata, Lavorazione, Evasa, Fallita, Respinta, Sospesa, Cancellata

### Mapping campi essenziali (parser)

| CSV | Backend attr | Uso |
|---|---|---|
| `CID` | `cid` | Chiave tecnica diff |
| `Riferimento` | `riferimento` | UI |
| `Riferimento` (senza `!N`) | `riferimento_base` | Chiave logica consegna fisica |
| `Catena` | `catena` | Breakdown catene + chiave logica |
| `Punto Vendita` | `punto_vendita` | Drill-down |
| `Stato` | `stato` | KPI, colori, audio |
| `Furgone` | `furgone` | Card per targa |
| `Data Consegna` | `data_consegna` | Filtro oggi |
| `Ora Da` / `Ora A` | `ora_da` / `ora_a` | Puntualità |
| `Data Cons. Reale` | `data_cons_reale` | Puntualità (timestamp evasione) |
| `Modifica` | `modifica` | Timestamp ultima modifica |
| `Tipo Riga` | filtro | Solo `=1` |
| `Causale` | `causale` | Motivo fallimento |
| `Note Trasportatore` | `note_trasportatore` | Contesto eventi negativi |
| `Cognome` / `Nome` | `cognome` / `nome` | UI |
| `Città` / `Provincia` | `citta` / `provincia` | UI |
| `Tipo Prodotto` / `Marca` / `Modello` | `tipo_prodotto` / `marca` / `modello` | Drill-down |
| `Colli` | `colli` | Count colli per consegna |
| `Proprieta Furgone` | `proprieta_furgone` | Flotta propria vs padroncino |
| Tutti gli altri (~100) | `raw_extra` | Conservati per moduli futuri |

---

## 7. Endpoint API

| Endpoint | Metodo | Cosa fa |
|---|---|---|
| `/` | GET | Frontend SPA |
| `/api/status` | GET | Healthcheck + info poller |
| `/api/today` | GET | Snapshot stato corrente (KPI + targhe + catene) |
| `/api/events?since=N` | GET | Eventi dal timestamp |
| `/api/events/recent?limit=20` | GET | Ultimi N eventi |
| `/api/delivery/{cid}` | GET | Drill-down singola consegna + colli correlati |
| `/ws/events` | WS | Push real-time eventi |

Eventi tipizzati emessi:
- `delivery.added` (consegna nuova in giornata)
- `delivery.status_changed` (con `old_stato`, `new_stato`, eventuale `puntualita`)
- `delivery.removed` (consegna sparita dal CSV — raro)

---

## 8. Comandi operativi essenziali

```bash
cd /home/antonio-pascarella/projects/sc-dashboard

# Stato + log
docker ps --filter "name=sc-dashboard"
docker logs -f sc-dashboard

# Restart
docker compose restart

# Stop / Start
docker compose down
docker compose up -d

# Rebuild dopo modifiche al codice
docker compose build && docker compose up -d

# Backup dati persistenti
tar -czf "backup-sc-dashboard-$(date +%Y%m%d).tar.gz" data/

# Health
curl -s http://100.98.10.55:8765/api/status
```

---

## 9. Cose ancora da fare (backlog)

### High priority (post-SFTP)
- [ ] Switch produzione a `DATA_SOURCE=sftp` quando IT pronta
- [ ] Eventuale tuning `POLL_INTERVAL` se 120s troppo aggressivo per IT

### Medium priority
- [ ] **Vista settimana / mese** con Chart.js (line/bar charts) — già nel design v0.5
- [ ] **Vista puntualità dedicata** con breakdown per catena/targa/fascia oraria
- [ ] **Export PDF** report mensile puntualità per committenti (standard nostro DS, `~/.local/bin/wkhtmltopdf`)
- [ ] Pulizia cron `events_log` (delete > 90 giorni)

### Low priority / future
- [ ] **Fase 2 Mission Control**: schermo gemello ufficio traffico, vista specializzata fallite/respinte/ritardi
- [ ] **Modulo Valorizzazione live** (riuso engine fatturazione-sc per €/giornata per catena)
- [ ] **Modulo RAEE**: counter ritiri R1-R4
- [ ] **Modulo Alert SLA**: consegne ferme da > X minuti
- [ ] **Modulo Meteo**: incrocio previsioni / consegne fallite

---

## 10. Sessioni precedenti / cronologia

### 2026-05-12 (oggi) — Brainstorming + Build + Deploy prod
- Brainstorming completo architettura
- Analisi CSV reale TransitPoint (130 colonne, 408 righe)
- Decisione palette, layout, audio, puntualità
- Build backend + frontend completo da zero
- Test loop replay (mock)
- Fix bug: `consegne == colli` (era contava CID invece di consegne fisiche)
- Aggiunta sezione catene
- **Dockerizzazione + deploy produzione su Tailscale `100.98.10.55:8765`**
- Push GitHub (vedi sezione 11)

---

## 11. GitHub

Repository: `Calamity955/sc-live-dashboard` (pubblico)
URL: https://github.com/Calamity955/sc-live-dashboard

File **NON committati** (sensibili o specifici di questa macchina):
- `.env.prod` — contiene path/configurazioni locali
- `data/` — DB SQLite e CSV
- `keys/` — chiavi SSH (se presenti)
- `samples/tp_*.csv` — CSV reali (contengono nominativi clienti)

Vedi `.gitignore` per la lista completa.

---

## 12. Persona di Atlas (operative)

Quando Calamity scrive su questo progetto in sessioni future:
- Risposte concrete, niente fluff "AI-cringe"
- Non promettere, esegui
- Se SFTP fallisce: log esplicito + rollback + chiedi info specifiche all'IT
- Non toccare la palette colori, gli stati o le chiavi senza chiedere conferma esplicita
- Backup `data/` prima di qualsiasi modifica al DB

---

**FINE DOCUMENTO.** Tutto il necessario per continuare il progetto è qui. Quando Calamity manderà le credenziali SFTP: vai alla sezione 2 ed esegui i passi.
