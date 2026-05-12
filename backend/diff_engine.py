"""Diff engine: confronta due snapshot e produce eventi tipizzati."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from .models import Delivery, Event


def _parse_dt(value: str) -> datetime | None:
    """Parsa formati TP: 'YYYY-MM-DD HH:MM:SS' o 'YYYY-MM-DD HH:MM'."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _combine_date_time(date_str: str, time_str: str) -> datetime | None:
    """Combina Data Consegna + Ora A/Da in un datetime."""
    if not date_str or not time_str:
        return None
    date_str = date_str.strip()
    time_str = time_str.strip()
    # Time può essere "HH:MM:SS" o "HH:MM"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    return None


def compute_puntualita(d: Delivery) -> tuple[str | None, int | None]:
    """Calcola flag puntualità per consegne Evase con Data Cons. Reale.

    Ritorna (flag, delta_minutes) o (None, None) se non applicabile.
    """
    if d.stato != "Evasa" or not d.data_cons_reale:
        return None, None
    real = _parse_dt(d.data_cons_reale)
    ora_a = _combine_date_time(d.data_consegna, d.ora_a)
    ora_da = _combine_date_time(d.data_consegna, d.ora_da)
    if real is None or ora_a is None:
        return None, None
    delta = (real - ora_a).total_seconds() / 60.0  # minuti
    if ora_da and real < ora_da:
        return "anticipata", int(delta)
    if delta <= 10:
        return "in_fascia", int(delta)
    if delta <= 40:
        return "ritardo_lieve", int(delta)
    return "ritardo_grave", int(delta)


def diff_snapshots(
    old: dict[str, Delivery],
    new: dict[str, Delivery],
    *,
    silent: bool = False,
    ts: datetime | None = None,
) -> list[Event]:
    """Confronta vecchio vs nuovo per CID e genera eventi.

    Args:
        silent: se True, marca tutti gli eventi come audio_silent (baseline iniziale).
    """
    ts = ts or datetime.now()
    events: list[Event] = []

    old_keys = set(old.keys())
    new_keys = set(new.keys())

    # Added
    for cid in new_keys - old_keys:
        d = new[cid]
        events.append(
            Event(
                ts=ts,
                type="delivery.added",
                cid=cid,
                targa=d.furgone,
                catena=d.catena,
                cliente=f"{d.cognome} {d.nome}".strip(),
                new_stato=d.stato,
                audio_silent=silent,
            )
        )

    # Removed
    for cid in old_keys - new_keys:
        d = old[cid]
        events.append(
            Event(
                ts=ts,
                type="delivery.removed",
                cid=cid,
                targa=d.furgone,
                catena=d.catena,
                cliente=f"{d.cognome} {d.nome}".strip(),
                old_stato=d.stato,
                audio_silent=silent,
            )
        )

    # Status changed
    for cid in old_keys & new_keys:
        o = old[cid]
        n = new[cid]
        if o.stato != n.stato:
            flag, delta = compute_puntualita(n)
            events.append(
                Event(
                    ts=ts,
                    type="delivery.status_changed",
                    cid=cid,
                    targa=n.furgone,
                    catena=n.catena,
                    cliente=f"{n.cognome} {n.nome}".strip(),
                    old_stato=o.stato,
                    new_stato=n.stato,
                    puntualita=flag,
                    delta_minutes=delta,
                    audio_silent=silent,
                )
            )

    return events
