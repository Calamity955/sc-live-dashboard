"""FastAPI app entry point per SC Live Dashboard."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import Database
from .diff_engine import compute_puntualita, diff_snapshots
from .models import Delivery
from .parser import deliveries_by_cid
from .source import Source, build_source_from_env
from .ws import WSManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sc-dashboard")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "10"))
DB_PATH = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "sc.db"))


class State:
    """Stato globale dell'app (poller, source, db, ws)."""

    db: Database
    source: Source
    ws: WSManager
    last_mtime: float | None = None
    last_snapshot: dict[str, Delivery] = {}
    baseline_loaded: bool = False
    poller_task: asyncio.Task | None = None


state = State()


async def poller_loop() -> None:
    """Loop di polling: rileva mtime change, ricalcola diff, broadcast."""
    log.info("Poller started (interval=%.1fs)", POLL_INTERVAL)
    while True:
        try:
            mtime = await state.source.current_mtime()
            if mtime is not None and mtime != state.last_mtime:
                deliveries = await state.source.load()
                new_map = deliveries_by_cid(deliveries)

                silent = not state.baseline_loaded
                events = diff_snapshots(state.last_snapshot, new_map, silent=silent)
                events = state.db.add_events(events)

                state.db.replace_current(deliveries)
                state.last_snapshot = new_map
                state.last_mtime = mtime
                state.baseline_loaded = True

                non_silent = [e for e in events if not e.audio_silent]
                log.info(
                    "Snapshot reloaded: %d deliveries | events=%d (audio=%d) | mtime=%.0f",
                    len(deliveries),
                    len(events),
                    len(non_silent),
                    mtime,
                )

                # Broadcast new events
                for e in non_silent:
                    await state.ws.broadcast({"kind": "event", "data": e.model_dump(mode="json")})
                # And a snapshot-refresh ping (lets clients refetch /api/today)
                if events:
                    await state.ws.broadcast({"kind": "snapshot_refreshed", "ts": datetime.now().isoformat()})

                # ---- Archiviazione storico ----
                await _archive_snapshot(deliveries)
        except Exception as exc:  # pragma: no cover
            log.exception("Poller error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.db = Database(DB_PATH)
    state.source = build_source_from_env()
    state.ws = WSManager()
    state.last_snapshot = deliveries_by_cid(state.db.load_current())
    state.baseline_loaded = bool(state.last_snapshot)
    state.poller_task = asyncio.create_task(poller_loop())
    log.info("App startup complete. Source=%s DB=%s", type(state.source).__name__, DB_PATH)
    try:
        yield
    finally:
        if state.poller_task:
            state.poller_task.cancel()
            try:
                await state.poller_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="SC Live Dashboard", version="0.5.0", lifespan=lifespan)


# ---------- helpers ----------

def _stato_consegna_fisica(states: list[str]) -> str:
    """Determina lo stato 'rappresentativo' di una consegna fisica multi-collo.

    Priorità worst-first: se almeno un collo è in stato problematico, prevale.
    Altrimenti: se tutti evasi → Evasa; se misti aperto/chiuso → Lavorazione.
    """
    if not states:
        return "—"
    priority = ["Fallita", "Respinta", "Sospesa", "Lavorazione", "Programmata", "Evasa", "Cancellata"]
    sset = set(states)
    for p in priority:
        if p in sset:
            if p == "Evasa" and len(sset) == 1:
                return "Evasa"
            if p == "Evasa":
                continue
            return p
    return states[0]


def _build_today_payload(deliveries: list[Delivery]) -> dict[str, Any]:
    """Aggrega snapshot.

    Distinzione cruciale:
    - **consegna fisica** = chiave (catena, riferimento_base)
    - **collo / riga** = un CID (riga Tipo Riga=1 del CSV)

    Una consegna fisica multi-collo viene conteggiata UNA SOLA volta nei KPI
    e nelle card per targa. Il dettaglio dei colli resta accessibile via
    /api/delivery/{cid} (related).
    """
    # Raggruppa righe per chiave logica consegna fisica
    consegne_fisiche: dict[tuple[str, str], dict[str, Any]] = {}
    fisica_senza_rif: list[Delivery] = []  # consegne senza riferimento_base (trattate singole)

    for d in deliveries:
        if d.riferimento_base:
            key = (d.catena or "", d.riferimento_base)
            grp = consegne_fisiche.setdefault(key, {
                "key": key,
                "deliveries": [],
                "targa": d.furgone or "—",
            })
            grp["deliveries"].append(d)
        else:
            fisica_senza_rif.append(d)

    # Per ogni consegna fisica calcola: stato rappresentativo, targa, ecc.
    consegne_list: list[dict[str, Any]] = []
    for grp in consegne_fisiche.values():
        ds = grp["deliveries"]
        states = [x.stato for x in ds]
        stato_repr = _stato_consegna_fisica(states)
        # Targa: prendi quella della maggior parte dei colli (di solito sono tutti su stessa targa)
        targhe_c = {}
        for x in ds:
            t = x.furgone or "—"
            targhe_c[t] = targhe_c.get(t, 0) + 1
        targa_repr = max(targhe_c.items(), key=lambda kv: kv[1])[0]
        consegne_list.append({
            "catena": ds[0].catena,
            "riferimento_base": ds[0].riferimento_base,
            "targa": targa_repr,
            "stato": stato_repr,
            "n_colli": len(ds),
            "cids": [x.cid for x in ds],
            "all_states": states,
            "deliveries": ds,
        })

    # Consegne senza riferimento_base: ognuna conta come singola
    for d in fisica_senza_rif:
        consegne_list.append({
            "catena": d.catena,
            "riferimento_base": None,
            "targa": d.furgone or "—",
            "stato": d.stato,
            "n_colli": 1,
            "cids": [d.cid],
            "all_states": [d.stato],
            "deliveries": [d],
        })

    # KPI globali sulle consegne fisiche
    kpi_states = {"Programmata": 0, "Lavorazione": 0, "Evasa": 0, "Fallita": 0,
                  "Respinta": 0, "Sospesa": 0, "Cancellata": 0}
    for c in consegne_list:
        kpi_states[c["stato"]] = kpi_states.get(c["stato"], 0) + 1

    # Puntualità: calcolata sulle consegne fisiche "Evasa" (tutti i colli evasi).
    # Usa il delta massimo tra i colli evasi della consegna fisica.
    puntualita_in = 0
    puntualita_tot = 0
    for c in consegne_list:
        if c["stato"] != "Evasa":
            continue
        flags = []
        for d in c["deliveries"]:
            if d.stato != "Evasa":
                continue
            flag, _ = compute_puntualita(d)
            if flag is not None:
                flags.append(flag)
        if not flags:
            continue
        puntualita_tot += 1
        # priorità worst-first sulle bandierine: se anche solo uno è in ritardo grave, contiamo grave
        if "ritardo_grave" in flags:
            pass  # non in fascia
        elif "ritardo_lieve" in flags:
            pass
        else:
            puntualita_in += 1

    # Aggregazione per CATENA (sulle consegne fisiche)
    by_catena: dict[str, dict[str, Any]] = {}
    for c in consegne_list:
        cat = c["catena"] or "—"
        bucket = by_catena.setdefault(cat, {
            "catena": cat,
            "total": 0,
            "colli": 0,
            "by_stato": {},
            "by_puntualita": {},
        })
        bucket["total"] += 1
        bucket["colli"] += c["n_colli"]
        bucket["by_stato"][c["stato"]] = bucket["by_stato"].get(c["stato"], 0) + 1
        # Conteggio puntualità per catena
        flag, _ = compute_puntualita(d)
        if flag in ("in_fascia", "anticipata"):
            bucket.setdefault("by_puntualita", {})["in_orario"] = bucket["by_puntualita"].get("in_orario", 0) + 1
        elif flag == "ritardo_lieve":
            bucket.setdefault("by_puntualita", {})["ritardo_lieve"] = bucket["by_puntualita"].get("ritardo_lieve", 0) + 1
        elif flag == "ritardo_grave":
            bucket.setdefault("by_puntualita", {})["ritardo_grave"] = bucket["by_puntualita"].get("ritardo_grave", 0) + 1
        bucket.setdefault("by_puntualita", {})["totale"] = bucket["by_puntualita"].get("totale", 0) + 1

    catene = list(by_catena.values())

    # Funzione di ranking per catene (worst-first)
    def _rank_catena(b: dict) -> tuple[int, int, int, str]:
        s = b["by_stato"]
        return (
            -s.get("Fallita", 0),
            -s.get("Respinta", 0),
            -b["total"],
            b["catena"],
        )

    catene.sort(key=_rank_catena)

    # Calcolo percentuale puntualità per ogni catena
    for c in catene:
        punct = c.get("by_puntualita", {})
        tot = punct.get("totale", 0)
        in_orario = punct.get("in_orario", 0)
        c["puntualita_pct"] = round(in_orario / tot * 100) if tot else None

    catene.sort(key=_rank_catena)

    # Aggregazione per targa (sulle consegne fisiche)
    by_targa: dict[str, dict[str, Any]] = {}
    for c in consegne_list:
        t = c["targa"]
        bucket = by_targa.setdefault(t, {
            "targa": t,
            "total": 0,
            "colli": 0,
            "by_stato": {},
            "consegne": [],
        })
        bucket["total"] += 1
        bucket["colli"] += c["n_colli"]
        bucket["by_stato"][c["stato"]] = bucket["by_stato"].get(c["stato"], 0) + 1
        bucket["consegne"].append({
            "catena": c["catena"],
            "riferimento_base": c["riferimento_base"],
            "stato": c["stato"],
            "n_colli": c["n_colli"],
            "cids": c["cids"],
        })

    targhe = list(by_targa.values())

    def _rank(b: dict) -> tuple[int, int, int, str]:
        s = b["by_stato"]
        return (
            -s.get("Fallita", 0),
            -s.get("Respinta", 0),
            -(s.get("Programmata", 0) + s.get("Lavorazione", 0)),
            b["targa"],
        )
    targhe.sort(key=_rank)

    total_colli = sum(c["n_colli"] for c in consegne_list)
    total_consegne = len(consegne_list)

    return {
        "ts": datetime.now().isoformat(),
        # nomi nuovi e chiari
        "consegne_fisiche": total_consegne,
        "totale_colli": total_colli,
        # retrocompatibilità (alias)
        "total_deliveries": total_consegne,
        "total_colli": total_colli,
        "kpi": {
            "programmata": kpi_states.get("Programmata", 0),
            "lavorazione": kpi_states.get("Lavorazione", 0),
            "evasa": kpi_states.get("Evasa", 0),
            "fallita": kpi_states.get("Fallita", 0),
            "respinta": kpi_states.get("Respinta", 0),
            "sospesa": kpi_states.get("Sospesa", 0),
            "cancellata": kpi_states.get("Cancellata", 0),
            "puntualita_in": puntualita_in,
            "puntualita_tot": puntualita_tot,
            "puntualita_pct": (puntualita_in / puntualita_tot * 100) if puntualita_tot else None,
        },
        "targhe": targhe,
        "catene": catene,
    }


# ---------- REST ----------

@app.get("/api/today")
def api_today() -> dict[str, Any]:
    deliveries = state.db.load_current()
    return _build_today_payload(deliveries)


@app.get("/api/events")
def api_events(since: int = 0, limit: int = 200) -> dict[str, Any]:
    events = state.db.events_since(since, limit=limit)
    return {"events": [e.model_dump(mode="json") for e in events]}


@app.get("/api/events/recent")
def api_events_recent(limit: int = 20) -> dict[str, Any]:
    events = state.db.recent_events(limit=limit)
    return {"events": [e.model_dump(mode="json") for e in events]}


@app.get("/api/delivery/{cid}")
def api_delivery(cid: str) -> dict[str, Any]:
    d = state.db.get_delivery(cid)
    if not d:
        raise HTTPException(status_code=404, detail=f"CID {cid} non trovato")
    # collega "colli" stesso Riferimento_base + Catena
    related = [
        rel.model_dump(mode="json")
        for rel in state.db.load_current()
        if rel.cid != cid
        and rel.riferimento_base
        and rel.riferimento_base == d.riferimento_base
        and rel.catena == d.catena
    ]
    flag, delta = compute_puntualita(d)
    return {
        "delivery": d.model_dump(mode="json"),
        "puntualita": flag,
        "delta_minutes": delta,
        "related": related,
    }


@app.get("/api/catena/{catena}")
def api_catena(catena: str) -> dict[str, Any]:
    # decodifica URL
    catena_decoded = catena.replace("_", "/").replace("_", " ")
    all_deliveries = state.db.load_current()
    # filtra per catena (match esatto o decoded)
    matches = [d for d in all_deliveries if d.catena == catena or d.catena == catena_decoded]
    if not matches:
        raise HTTPException(status_code=404, detail=f"Catena {catena} non trovata")
    # aggrega per stato
    by_stato: dict[str, int] = {}
    for d in matches:
        by_stato[d.stato] = by_stato.get(d.stato, 0) + 1
    # lista dettagli consegne
    deliveries_detail = [
        {
            "cid": d.cid,
            "riferimento": d.riferimento,
            "riferimento_base": d.riferimento_base,
            "stato": d.stato,
            "n_colli": d.colli,
            "punto_vendita": d.punto_vendita,
            "furgone": d.furgone,
            "citta": d.citta,
            "provincia": d.provincia,
        }
        for d in matches
    ]
    return {
        "catena": catena,
        "total": len(matches),
        "colli": sum(d.colli for d in matches),
        "by_stato": by_stato,
        "deliveries": deliveries_detail,
    }


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return {
        "ok": True,
        "last_mtime": state.last_mtime,
        "baseline_loaded": state.baseline_loaded,
        "snapshot_size": len(state.last_snapshot),
        "ws_clients": state.ws.client_count,
        "poll_interval": POLL_INTERVAL,
    }


# ---------- WebSocket ----------

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await state.ws.connect(ws)
    # invia snapshot iniziale al client
    try:
        await ws.send_json({"kind": "hello", "ts": datetime.now().isoformat()})
        while True:
            # mantieni vivo: aspetta eventuali ping dal client (ignorati)
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await state.ws.disconnect(ws)


# ---------- Static frontend ----------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ---------- Storico -----
HISTORY_DIR = Path(os.getenv("DATA_PATH", "./data/csv")).parent / "history"


async def _archive_snapshot(deliveries: list[Delivery]) -> None:
    """Salva lo snapshot su disco per lo storico: primo + ultimo del giorno."""
    if not deliveries:
        return
    # determina la data di oggi (Europe/Rome)
    today = datetime.now().strftime("%Y-%m-%d")
    now_ts = datetime.now().strftime("%H%M%S")
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # ---- CSV ----
    csv_lines = ["CID,Riferimento,Riferimento_base,Catena,Punto Vendita,Stato,Furgone,"
                "Data Consegna,Ora Da,Ora A,Data Cons. Reale,Citta,Provincia,"
                "Tipo Prodotto,Marca,Modello,Colli,Causale,Note Trasportatore"]
    for d in deliveries:
        csv_lines.append(
            f"{d.cid},{d.riferimento},{d.riferimento_base},{d.catena},{d.punto_vendita},"
            f"{d.stato},{d.furgone},{d.data_consegna},{d.ora_da},{d.ora_a},{d.data_cons_reale},"
            f"{d.citta},{d.provincia},{d.tipo_prodotto},{d.marca},{d.modello},"
            f"{d.colli},{d.causale},{d.note_trasportatore}"
        )
    csv_content = "\n".join(csv_lines)

    # ---- Primo del giorno ----
    first_path = HISTORY_DIR / f"{today}_FIRST.csv"
    if not first_path.exists():
        first_path.write_text(csv_content, encoding="utf-8")
        log.info("Archivio primo del giorno: %s", first_path.name)

    # ---- Ultimo del giorno (sovrascrive) ----
    last_path = HISTORY_DIR / f"{today}.csv"
    last_path.write_text(csv_content, encoding="utf-8")


# ---------- Storico: API ---------


@app.get("/api/days")
def api_days() -> dict[str, Any]:
    """Lista giorni disponibili nello storico."""
    if not HISTORY_DIR.exists():
        return {"days": []}
    # cerca tutti i file *_FIRST.csv (uno per giorno)
    files = sorted(HISTORY_DIR.glob("*_FIRST.csv"), reverse=True)
    days = [f.stem[:10] for f in files]  # YYYY-MM-DD
    return {"days": days}


@app.get("/api/day/{date}")
def api_day(date: str) -> dict[str, Any]:
    """Carica snapshot di un giorno specifico. date = YYYY-MM-DD."""
    # validazione formato
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise HTTPException(status_code=400, detail="Formato: YYYY-MM-DD")

    first_path = HISTORY_DIR / f"{date}_FIRST.csv"
    last_path = HISTORY_DIR / f"{date}.csv"

    if not first_path.exists() and not last_path.exists():
        raise HTTPException(status_code=404, detail=f"Nessuno storico per {date}")

    # usa l'ultimo disponibile (o il primo se l'ultimo non c'è)
    src_path = last_path if last_path.exists() else first_path
    if not src_path.exists():
        raise HTTPException(status_code=404, detail=f"File non trovato per {date}")

    csv_text = src_path.read_text(encoding="utf-8")
    lines = csv_text.split("\n")
    if len(lines) < 2:
        return {"date": date, "consegne": []}


    header = lines[0].split(",")
    deliveries = []
    for line in lines[1:]:
        if not line.strip():
            continue
        vals = line.split(",")
        if len(vals) < len(header):
            continue
        row = dict(zip(header, vals))
        # converte campi numerici
        try:
            row["colli"] = int(row.get("colli", 0) or 0)
            row["cid"] = str(row.get("cid", ""))
        except (ValueError, TypeError):
            pass
        deliveries.append(row)

    # ricalcola KPI
    kpi = {"programmata": 0, "lavorazione": 0, "evasa": 0, "fallita": 0, "respinta": 0, "sospesa": 0, "cancellata": 0}
    for d in deliveries:
        s = d.get("stato", "")
        if s in kpi:
            kpi[s] += 1

    # aggrega per catena
    by_catena: dict[str, dict] = {}
    for d in deliveries:
        cat = d.get("catena", "—") or "—"
        b = by_catena.setdefault(cat, {"catena": cat, "total": 0, "colli": 0, "by_stato": {}})
        b["total"] += 1
        b["colli"] += d.get("colli", 0)
        s = d.get("stato", "")
        b["by_stato"][s] = b["by_stato"].get(s, 0) + 1
    catene = sorted(by_catena.values(), key=lambda x: -x["total"])


    # aggrega per targa
    by_targa: dict[str, dict] = {}
    for d in deliveries:
        t = d.get("Furgone", d.get("furgone", "—")) or "—"
        b = by_targa.setdefault(t, {"targa": t, "total": 0, "colli": 0, "by_stato": {}})
        b["total"] += 1
        b["colli"] += d.get("colli", 0)
        s = d.get("stato", "")
        b["by_stato"][s] = b["by_stato"].get(s, 0) + 1
    targhe = sorted(by_targa.values(), key=lambda x: -x["total"])

    return {
        "date": date,
        "source": src_path.name,
        "consegne_fisiche": len(deliveries),
        "totale_colli": sum(d.get("colli", 0) for d in deliveries),
        "kpi": kpi,
        "catene": catene,
        "targhe": targhe[:30],  # prime 30 targhe
    }
    log.debug("Archivio ultimo del giorno: %s", last_path.name)
