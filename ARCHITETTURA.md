# SC Live Dashboard — Architettura e Analisi

> **Status:** Draft v0.5 — 2026-05-12
> **Owner:** Calamity / Atlas
> **Scope:** Dashboard real-time per monitoraggio operativo Servizi Campania su schermo ufficio

---

## 1. Obiettivo

Schermo "war-room" sempre acceso in ufficio (collegato al PC di Calamity) che mostra in tempo reale lo stato operativo delle consegne TransitPoint. Deve:

- dare una **fotografia istantanea della giornata** (consegne programmate, evase, fallite, respinte) con vista per targa/squadra;
- permettere **navigazione**: dal day-view alla settimana/mese, e drill-down per catena;
- emettere **feedback audio** discreti su eventi rilevanti (consegna OK, fallita, respinta);
- essere **estendibile a moduli** (KPI mensili, RAEE, fatturazione live, ecc.).

---

## 2. Vincoli e ipotesi (aggiornati v0.2)

- **Sorgente dati:** WSD TransitPoint (SOAP) — ambiente **PRODUZIONE** (uso read-only).
- **Polling rate:** **15 secondi** sul day-view, **60s** sulle viste aggregate. WSD non è push; 15s è impercettibile per l'operatore e gentile per il backend.
- **Hosting:** VPS OVH già in uso (stesso dove gira fatturazione-sc). Container Docker dedicato, reverse proxy su sottodominio interno.
- **Accesso:** solo rete interna / Tailscale, niente esposizione pubblica. Credenziali WSD in `.env` lato server, mai nel frontend.
- **Browser target:** TV ufficio Calamity (fase 1) + futuro schermo ufficio traffico (fase 2, multi-postazione).
- **Targhe dinamiche:** la mappa targhe NON è statica — viene derivata ogni giorno dalle consegne effettivamente programmate. Range tipico **10-30 targhe attive contemporaneamente**, il layout deve scalare graziosamente in entrambi i casi.
- **Notifiche:** solo audio in loco. No Telegram/email.
- **Scrittura su TP:** mai. Solo `read` sul WSD.

---

## 3. Architettura logica

```
┌────────────────────────────────────────────────────────────────┐
│                       BROWSER (kiosk + desktop)                │
│   Dashboard SPA (HTML/JS) — WebSocket + REST + Audio API       │
└──────────────▲────────────────────────────────────▲────────────┘
               │ WS (push eventi)                   │ REST (drill-down)
┌──────────────┴────────────────────────────────────┴────────────┐
│                     BACKEND (FastAPI + asyncio)                │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Poller WSD  │→ │ Diff engine  │→ │ Event bus (WS push)  │  │
│  │ (zeep)      │  │ (state diff) │  │                      │  │
│  └─────────────┘  └──────┬───────┘  └──────────────────────┘  │
│                          ▼                                     │
│                  ┌──────────────────┐                          │
│                  │ Cache (SQLite /  │  ← storico giornaliero  │
│                  │ Redis opzionale) │     + KPI settimanali   │
│                  └──────────────────┘                          │
└────────────────────────────────────────────────────────────────┘
                          │
                          ▼
                ┌──────────────────┐
                │  TransitPoint    │
                │  WSD SOAP API    │
                └──────────────────┘
```

### Componenti

1. **Poller WSD** — task asyncio che ogni 15s interroga TP. Due strategie da combinare:
   - `EXPORT_EVENTS` (batch eventi, già pensato per polling 10-15min — utile per ricostruire cronologia);
   - **Query mirate sulle consegne del giorno** (per la fotografia istantanea: programmate / evase / fallite / respinte). Da definire con te quale metodo WSD specifico useremo per la lista giornaliera — sul protocollo abbiamo le porte ma non un `get_deliveries_by_date` esplicito: serve verifica sul WSDL reale con le credenziali.

2. **Diff engine** — confronta lo snapshot corrente con il precedente. Genera eventi tipizzati:
   - `delivery.completed` (Evasa)
   - `delivery.failed` (Fallita)
   - `delivery.refused` (Respinta)
   - `delivery.status_changed` (qualsiasi altro cambio)
   - `vehicle.closed_day` (targa che chiude la giornata = tutte le consegne in stato finale)

3. **Cache locale** — SQLite con due tabelle:
   - `deliveries_snapshot` (stato attuale, rigenerato ogni poll)
   - `events_log` (storico append-only per timeline + audio replay + KPI)

4. **API REST** — per drill-down (settimanale, mensile, per catena) senza dover ricaricare.

5. **WebSocket** — push immediato degli eventi al frontend per:
   - aggiornare contatori senza refresh;
   - triggerare il suono giusto;
   - animare badge / toast.

6. **Frontend SPA** — singola pagina con tab/sezioni. Tecnologia: **HTML + Alpine.js + Chart.js** (leggero, niente build complesso, manutenibile). Tailwind via CDN per lo styling.

---

## 4. Layout della Dashboard

### 4.1 Header (sempre visibile)
- Data + ora corrente
- Indicatore connessione WSD (verde/giallo/rosso + ultimo poll OK)
- Selettore vista: **Oggi | Settimana | Mese | Catene**
- Toggle audio (on/off + volume)

### 4.2 Vista "Oggi" (default — kiosk principale)

**Riga 1 — KPI di giornata (5 tile grandi):**
- 📦 **Programmate oggi** (totale)
- ✅ **Evase** (verde, con %)
- ❌ **Fallite** (rosso, con %)
- ↩️ **Respinte** (arancio, con %)
- ⏱️ **Puntualità** (blu, % consegne evase in fascia oraria contrattuale)

**Riga 2 — Griglia adattiva per targa (cuore della dashboard):**
La griglia si **auto-dimensiona** in base al numero di targhe attive del giorno. Tre modalità di rendering automatiche:

| N° targhe | Modalità | Dimensione card | Layout |
|---|---|---|---|
| 1-12 | **Comfort** | Grande (~280×180px) | 3-4 colonne, dettagli completi |
| 13-20 | **Compact** | Media (~200×140px) | 5 colonne, dettagli essenziali |
| 21-30+ | **Dense** | Piccola (~150×100px) | 6-7 colonne, solo numeri chiave + colore stato |

La modalità viene scelta dal frontend in base alla viewport + count targhe (CSS Grid con `auto-fit` + breakpoint logici). L'utente può forzare manualmente una modalità.

**Contenuto card (decresce con la densità):**
- *Comfort:* targa, eventuale nome squadra, progress bar evase/totali, conteggi separati (in corso · evase · fallite · respinte), ora prima/ultima consegna, badge stato giornata
- *Compact:* targa, progress bar, conteggi compatti (es. `12/18 ✅2❌1`), badge stato
- *Dense:* targa + grande indicatore percentuale + bordo colorato (verde/giallo/rosso) per leggibilità a distanza

**Ordinamento card:** configurabile, default suggerito = *targhe con problemi in alto* (fallite > respinte > programmate non ancora partite > in corso > chiuse).

**Click su card → drill-down** con lista consegne di quella targa (modale o pannello laterale).

**Mappa targhe:** derivata runtime dalle consegne del giorno. Eventuale label "nome squadra" può essere aggiunta in un file di mapping opzionale (`targhe_labels.json`) che arricchisce le card quando la targa è nota, senza essere bloccante.

**Riga 3 — Timeline eventi (live feed):**
Stream verticale degli ultimi 20 eventi, con timestamp, targa, cliente, stato. Stile "ticker". In modalità Dense la timeline può collassare in sidebar laterale per liberare spazio.

### 4.3 Vista "Settimana"
- Grafico a barre stacked per giorno (evase/fallite/respinte)
- Tabella per catena × giorno
- % success rate settimanale

### 4.4 Vista "Mese"
- Heatmap calendario (volume + success rate)
- Top 5 catene per volume
- Trend success rate (line chart)

### 4.5 Vista "Catene"
- Tabella catena → KPI (volume, success, tempo medio chiusura giornata)
- Filtro temporale (oggi / 7gg / 30gg)
- Drill-down catena → consegne / squadre coinvolte

---

## 5. Feedback audio

Suoni discreti, non invasivi (durata <1s). Funzionano lato browser → ogni postazione che apre la dashboard genera autonomamente i suoi suoni (fase 2: tutto l'ufficio traffico li sente sul proprio schermo).

Canali:

| Evento | Suono | Note |
|---|---|---|
| `delivery.completed` | "ping" delicato | Limite: max 1 ogni 3s per non spammare durante burst |
| `delivery.failed` | "buzz" basso | Sempre, anche durante burst |
| `delivery.refused` | "double-tap" | Sempre |
| `vehicle.closed_day` | "chime" lungo | Quando una targa chiude la giornata |
| `connection.lost` | "alert" ripetuto | Se polling WSD fallisce >2 cicli |

Implementazione: Web Audio API, file `.wav` precaricati. Toggle globale + per-tipo. **Quiet hours** opzionali (es. niente audio prima delle 8 / dopo le 20).

Coda audio con debounce per evitare cacofonia su burst di eventi (>5 in 10s → audio aggregato "burst ping").

---

## 6. Dati che servono da WSD (verifica con credenziali)

Per ogni consegna del giorno serve almeno:
- `delivery_id`, `store_order`
- `data_programmata`, `data_evasione` (o ultima modifica stato)
- `stato` (Evasa, Fallita, Respinta, Programmata, Assegnata, Nuova, …)
- `targa` o ID squadra
- `catena` (committente)
- `cliente` (nome + città, per toast/timeline)
- `luogo` (Cliente/Negozio) — utile per logica giornaliera
- `ora_appuntamento` (se presente)

**Punto aperto:** il WSDL pubblico documenta principalmente metodi di scrittura/lettura per consegna singola. Per il day-view serve un metodo di lista (per data o range). Quando mi dai le credenziali ispeziono il WSDL reale (`dev.transitpoint.us/services/wsd/wsd.wsdl`) e mappiamo i metodi disponibili. Se manca un metodo "list", l'alternativa è combinare `EXPORT_EVENTS` per il delta + cache locale che si auto-popola.

---

## 6bis. Modulo Puntualità (KPI critico)

Misura il rispetto delle fasce orarie contrattuali. Le consegne SC hanno **sempre** una fascia (`ora_da` / `ora_a`), nessuna eccezione.

**Parametri finali (v0.4):**
- **Tolleranza in fascia: +10 minuti** dopo `ora_a` → considerato ancora puntuale
- **Soglia ritardo lieve: fino a 30 minuti** dopo `ora_a + tolleranza`
- **Soglia ritardo grave: oltre 30 minuti** dopo `ora_a + tolleranza`

**Logica esatta:**
```
delta = ora_evasione - ora_a

if delta <= +10 min      → 🟢 in fascia (tolleranza inclusa)
elif delta <= +40 min    → 🟡 ritardo lieve
else                     → 🔴 ritardo grave

if ora_evasione < ora_da → 🔵 anticipata (tracciata ma non penalizzante)
```

Parametri in `puntualita.yaml`, modificabili senza ridistribuire il container.

**Note trasportatore come contesto:**
Quando una consegna ha stato negativo (fallita/respinta) o ritardo grave, la dashboard mostra automaticamente il campo `note_trasportatore` (se presente nel CSV) sia nel toast/timeline che nel drill-down della card. Così capisci subito il motivo senza dover aprire il gestionale.

**Flag salvato in `events_log` SQLite** per analisi storica.

**Audio dedicato:**
- Ritardo lieve: ping leggero informativo
- Ritardo grave: buzz arancio (attenzione)

**Visibilità:** KPI di giornata, card per targa, timeline eventi, drill-down.

**Vista "Puntualità" dedicata:**
- Selettore periodo (oggi/7gg/30gg/mese/custom)
- Breakdown per catena, targa, fascia oraria
- Trend giornaliero (line chart)
- Export CSV/Excel + PDF standard SC per report mensili a committenti

## 7. Estensibilità / Moduli futuri

Il backend espone un **event bus** e una **cache strutturata**: aggiungere moduli significa solo aggiungere consumer + tile/sezione nel frontend. Idee già allineate al tuo lavoro:

- **Modulo Fatturazione live**: contatori valorizzazione giornaliera per catena (riusa logica `engine.py` di fatturazione-sc).
- **Modulo RAEE**: contatori ritiri RAEE per categoria R1-R4.
- **Modulo Alert SLA**: consegne in ritardo / squadre ferme da X minuti.
- **Modulo Meteo**: incrocio con previsioni (giorni di pioggia = +fallite attese).
- **Modulo Padroncini**: KPI per padroncino (volume, success rate, valorizzazione).
- **Modulo HR**: alert su pattern anomali (es. squadra con >X% fallite oggi).

---

## 8. Sicurezza

- Credenziali WSD: `.env` lato container, mai esposte al browser.
- Dashboard accessibile solo via Tailscale o LAN.
- Reverse proxy con basic auth + IP allowlist come secondo livello.
- Audit log: ogni poll + ogni evento generato → `events_log`.
- Niente scrittura su TP da questa dashboard (read-only).

---

## 9. Stack tecnico proposto

| Layer | Scelta | Motivo |
|---|---|---|
| Backend | Python 3.12 + FastAPI + asyncio | Stesso stack di fatturazione-sc, riuso know-how |
| SOAP client | `zeep` | Standard de-facto, già citato nel protocollo WSD |
| DB locale | SQLite (WAL mode) | Zero-config, sufficiente per volumi giornalieri |
| WebSocket | FastAPI nativo | Niente dipendenze extra |
| Frontend | HTML + Alpine.js + Tailwind CDN + Chart.js | Leggero, manutenibile, no build step |
| Audio | Web Audio API + .wav locali | Compatibile kiosk Chromium |
| Container | Docker singolo (no compose) | Coerente con fatturazione-sc |
| Reverse proxy | Caddy o nginx esistente | Da definire con setup VPS |

---

## 10. Roadmap implementativa (proposta)

**Sprint 0 — Discovery (½ giornata)**
- Ricevere CSV di esempio da Calamity (export manuale dal gestionale)
- Analizzare tutte le colonne disponibili
- Mappa colonne: essenziali Sprint 1 / utili futuri / ignorabili
- Definire schema di input del backend (CSV-agnostico)
- Specifica finale per IT (PDF da girare)

**Sprint 1 — Backend MVP (1-2 giornate)**
- Poller WSD + diff engine + SQLite
- API REST `/today`, `/week`, WS `/events`
- Test con dati reali di un giorno qualsiasi

**Sprint 2 — Frontend "Oggi" (1-2 giornate)**
- Layout kiosk vista giornaliera
- Live update via WS
- Audio engine + suoni base

**Sprint 3 — Drill-down + viste aggregate (1-2 giornate)**
- Vista settimana/mese/catene
- Navigazione + filtri

**Sprint 4 — Hardening (1 giornata)**
- Reconnect WSD, gestione errori, alert connessione
- Deploy Docker su VPS + reverse proxy
- Mode kiosk auto-start

**Estensioni** → moduli futuri secondo priorità.

---

## 10bis. Modello dati e regole CSV (v0.5)

### Filtraggio righe

**Includere solo `Tipo Riga = 1`** (consegna principale).
Le righe `Tipo Riga = 2` e `Tipo Riga = 3` sono prelievi da negozio/magazzino generati automaticamente dal gestionale, **non rilevanti per la dashboard**. Vengono scartate dal parser.

### Chiavi

- **CID** (numerico, univoco) = chiave tecnica interna per il **diff engine**.
- **Catena + Riferimento base** (es. `Arcobaleno Hi-fi Srl + 10632`) = chiave logica della **consegna fisica**. Il `Riferimento` con suffissi `!1`, `!2`, `!3` rappresenta il multicollo dello stesso cliente; il `Riferimento base` rimuove il suffisso.

### Conteggi

La dashboard conta **consegne fisiche**, non colli:

> Sul tile principale: `168 consegne (359 colli)`

- **Consegne** = `Catena + Riferimento base` distinti
- **Colli** = CID distinti (righe Tipo Riga=1)
- Drill-down su una consegna fisica mostra **tutti i suoi colli** con marca, modello, prodotto.

### Stati: nomi preservati

Gli stati visualizzati in dashboard mantengono **esattamente** i nomi del CSV (no rinominazioni, no normalizzazioni). Stati rilevati nel sample:

`Programmata`, `Lavorazione`, `Evasa`, `Fallita`, `Respinta`, `Sospesa`, `Cancellata`

Nuovi stati eventuali futuri vengono accolti automaticamente con colore default (grigio) finché non viene assegnato.

### Palette colori stati (definitiva)

| Stato | Colore | Hex suggerito |
|---|---|---|
| Programmata | Fucsia | `#e91e63` |
| Lavorazione | Verde chiaro | `#a5d6a7` |
| Evasa | Verde scuro | `#2e7d32` |
| Fallita | Rosso | `#d32f2f` |
| Respinta | Giallo | `#fbc02d` |
| Sospesa | (da definire — default arancione neutro) | `#f57c00` |
| Cancellata | (grigio, non audio, non KPI) | `#9e9e9e` |

---

## 11. Decisioni prese (v0.5)

- ✅ **Sorgente dati**: CSV via SFTP (PROD WSD come opzione futura).
- ✅ **Filtro righe**: solo `Tipo Riga = 1`.
- ✅ **Chiave tecnica**: CID. **Chiave logica**: Catena + Riferimento base.
- ✅ **Conteggio**: consegne fisiche (con dettaglio colli su drill-down).
- ✅ **Stati**: preservati letteralmente dal CSV.
- ✅ **Palette colori**: fucsia/verdi/rosso/giallo come da specifica utente.
- ✅ **Puntualità**: tolleranza +10 min, ritardo lieve fino +40 min, grave oltre.
- ✅ **Note trasportatore**: integrate nel drill-down per consegne con problemi.
- ✅ **Mappa targhe**: dinamica, derivata ogni giorno dalle consegne TP. Layout adattivo per 10-30 targhe.
- ✅ **Notifiche Telegram**: no, solo audio in loco.
- ✅ **Fase 2 — Mission Control**: in futuro replica della dashboard sullo schermo dell'ufficio traffico. Stesso backend, multiple sessioni browser, ognuna con il proprio audio locale.

---

## 12. Domande ancora aperte

1. **Risoluzione TV ufficio**: quando puoi misurala (o dammi modello). Per scegliere bene tra Comfort/Compact di default.
2. **Quiet hours audio**: orari? Suggerisco 8:00-19:00 lun-sab.
3. **Lista catene**: vuoi tutte o filtrare (es. escludere committenti dormienti)?
4. **Drill-down per targa**: vuoi vedere anche dati cliente (nome/città) o solo store_order + stato?
5. **Dominio interno**: `dashboard.sc.local` via Tailscale o sottodominio reale tipo `dashboard.servizicampania.local`?
6. **Riconnessione automatica**: se la TV perde rete, deve ricaricare da sola al ritorno? (consiglio sì, watchdog JS)

---

## 13. Prossimo passo

Lo Sprint 0 si sblocca con le credenziali WSD PROD (mandamele via canale sicuro: file `.env` che salvi direttamente sul VPS, oppure messaggio cifrato che cancelliamo dopo l'uso).

Nello Sprint 0 farò:
1. Ispezione del WSDL reale
2. Mappa dei metodi disponibili per query lista consegne del giorno
3. Test connessione + 1 chiamata reale
4. Report con il piano definitivo per Sprint 1
