"""CSV parser agnostico per TransitPoint TP-export.

Vincoli:
- Separatore virgola, quote ", encoding UTF-8.
- Filtra solo righe Tipo Riga == "1" (consegne fisiche).
- Tutti i campi non mappati esplicitamente vanno in raw_extra.
- Chiave logica: (Catena, Riferimento_base) dove Riferimento_base = Riferimento senza suffisso !N.
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .models import Delivery

_REF_SUFFIX_RE = re.compile(r"![0-9]+$")

# Mapping intestazione CSV -> attributo Delivery
_FIELD_MAP: dict[str, str] = {
    "CID": "cid",
    "Riferimento": "riferimento",
    "Catena": "catena",
    "Punto Vendita": "punto_vendita",
    "Stato": "stato",
    "Furgone": "furgone",
    "Data Consegna": "data_consegna",
    "Ora Da": "ora_da",
    "Ora A": "ora_a",
    "Data Cons. Reale": "data_cons_reale",
    "Modifica": "modifica",
    "Luogo": "luogo",
    "Cognome": "cognome",
    "Nome": "nome",
    "Città": "citta",
    "Provincia": "provincia",
    "Tipo Prodotto": "tipo_prodotto",
    "Marca": "marca",
    "Modello": "modello",
    "Causale": "causale",
    "Note Trasportatore": "note_trasportatore",
    "Sorgente Consegna": "sorgente_consegna",
    "Colli": "colli",
    "Proprieta Furgone": "proprieta_furgone",
}


def _strip_ref_suffix(ref: str) -> str:
    """Rimuove suffisso !N dal Riferimento per ottenere la chiave logica."""
    return _REF_SUFFIX_RE.sub("", ref or "")


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0


def _row_to_delivery(row: dict) -> Delivery | None:
    """Converte una riga CSV in Delivery se Tipo Riga=1, altrimenti None."""
    if (row.get("Tipo Riga") or "").strip() != "1":
        return None
    kwargs: dict = {}
    extra: dict = {}
    for header, value in row.items():
        if header is None:
            continue
        attr = _FIELD_MAP.get(header)
        if attr is None:
            extra[header] = value
            continue
        if attr == "colli":
            kwargs[attr] = _to_int(value)
        else:
            kwargs[attr] = (value or "").strip()
    kwargs["riferimento_base"] = _strip_ref_suffix(kwargs.get("riferimento", ""))
    kwargs["raw_extra"] = extra
    if not kwargs.get("cid"):
        return None
    return Delivery(**kwargs)


def _filter_today(d: Delivery, today_str: str) -> bool:
    """True se la consegna ha Data Consegna == today (formato 'YYYY-MM-DD').

    Il campo Data Consegna nel CSV TP è 'YYYY-MM-DD' (eventualmente con HH:MM:SS).
    """
    raw = (d.data_consegna or "").strip()
    if not raw:
        return False
    return raw[:10] == today_str


def _stream_parse(reader: csv.DictReader, today_only: bool = False) -> list[Delivery]:
    out: list[Delivery] = []
    today_str = date.today().isoformat() if today_only else ""
    for row in reader:
        d = _row_to_delivery(row)
        if d is None:
            continue
        if today_only and not _filter_today(d, today_str):
            continue
        out.append(d)
    return out


def parse_csv(path: str | Path) -> list[Delivery]:
    """Parse del CSV TP da file. Filtra Tipo Riga=1.

    Se la env var `FILTER_TODAY=1` è attiva, scarta consegne con Data Consegna
    diversa da oggi (Europe/Rome via TZ del processo).
    """
    p = Path(path)
    today_only = os.getenv("FILTER_TODAY", "0") == "1"
    with p.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=",", quotechar='"')
        return _stream_parse(reader, today_only=today_only)


def parse_csv_bytes(data: bytes) -> list[Delivery]:
    """Parse del CSV TP da bytes (per SFTP). Stessa logica di parse_csv."""
    today_only = os.getenv("FILTER_TODAY", "0") == "1"
    text = data.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=",", quotechar='"')
    return _stream_parse(reader, today_only=today_only)


def deliveries_by_cid(deliveries: Iterable[Delivery]) -> dict[str, Delivery]:
    return {d.cid: d for d in deliveries}
