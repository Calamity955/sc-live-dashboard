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
        })
        bucket["total"] += 1
        bucket["colli"] += c["n_colli"]
        bucket["by_stato"][c["stato"]] = bucket["by_stato"].get(c["stato"], 0) + 1

    catene = list(by_catena.values())

    def _rank_catena(b: dict) -> tuple[int, int, int, str]:
        s = b["by_stato"]
        # worst-first: fallite > respinte > volume
        return (
            -s.get("Fallita", 0),
            -s.get("Respinta", 0),
            -b["total"],
            b["catena"],
        )
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
