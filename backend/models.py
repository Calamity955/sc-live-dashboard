"""Pydantic models for SC Live Dashboard."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Delivery(BaseModel):
    """A single parsed CSV row (Tipo Riga=1)."""

    cid: str
    riferimento: str = ""
    riferimento_base: str = ""
    catena: str = ""
    punto_vendita: str = ""
    stato: str = ""
    furgone: str = ""
    data_consegna: str = ""
    ora_da: str = ""
    ora_a: str = ""
    data_cons_reale: str = ""
    modifica: str = ""
    luogo: str = ""
    cognome: str = ""
    nome: str = ""
    citta: str = ""
    provincia: str = ""
    tipo_prodotto: str = ""
    marca: str = ""
    modello: str = ""
    causale: str = ""
    note_trasportatore: str = ""
    sorgente_consegna: str = ""
    colli: int = 0
    proprieta_furgone: str = ""
    raw_extra: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    """A diff event."""

    id: Optional[int] = None
    ts: datetime
    type: str  # delivery.added | delivery.status_changed | delivery.removed
    cid: str
    targa: str = ""
    catena: str = ""
    cliente: str = ""
    old_stato: Optional[str] = None
    new_stato: Optional[str] = None
    puntualita: Optional[str] = None  # in_fascia | ritardo_lieve | ritardo_grave | anticipata
    delta_minutes: Optional[int] = None
    audio_silent: bool = False


class Snapshot(BaseModel):
    """A full snapshot of deliveries at a given mtime."""

    mtime: float
    deliveries: list[Delivery]
