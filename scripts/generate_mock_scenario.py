#!/usr/bin/env python3
"""Genera 6 snapshot CSV progressivi a partire dal sample TP.

Output: data/csv/tp_snapshot_HHMMSS.csv (ordinabili alfabeticamente).

Step T0 = baseline (sample originale).
Step T+2..T+10 = modifiche progressive realistiche:
- alcune Programmate → Evase (con Data Cons. Reale plausibile: in fascia / lievi / gravi)
- 1-3 Programmate → Fallite (con Causale)
- 1 Programmata → Respinta
- una Lavorazione → Evasa
- aggiorna Modifica con nuovo timestamp
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "samples" / "tp_20260512_sample.csv"
OUT = ROOT / "data" / "csv"

CAUSALI = [
    "Cliente assente",
    "Indirizzo errato",
    "Rifiuto al ricevimento",
    "Cliente irreperibile",
    "Prodotto non conforme",
]

# Numero di transizioni per step
PROG_TO_EVASA_PER_STEP = 8
PROG_TO_FALLITA_PER_STEP = (1, 3)
PROG_TO_RESPINTA_PER_STEP = 1
LAV_TO_EVASA_PER_STEP = 1


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=",", quotechar='"')
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return fieldnames, rows


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=fieldnames, delimiter=",", quotechar='"', quoting=csv.QUOTE_ALL
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _combine(date_s: str, time_s: str) -> datetime | None:
    if not date_s or not time_s:
        return None
    try:
        return datetime.strptime(f"{date_s.strip()} {time_s.strip()}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(f"{date_s.strip()} {time_s.strip()}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None


def _make_real_for(row: dict[str, str], category: str) -> str:
    """Produce Data Cons. Reale plausibile data la categoria di puntualità."""
    date_s = row.get("Data Consegna", "")
    ora_a = _combine(date_s, row.get("Ora A", ""))
    ora_da = _combine(date_s, row.get("Ora Da", ""))
    if ora_a is None:
        # fallback: oggi pomeriggio
        ora_a = datetime.strptime(f"{date_s} 16:00:00", "%Y-%m-%d %H:%M:%S")
    if category == "in_fascia":
        delta = random.randint(-15, 8)
        base = ora_a if (ora_da is None or random.random() > 0.4) else ora_da + timedelta(minutes=random.randint(0, 30))
        return _fmt_dt(base + timedelta(minutes=delta))
    if category == "ritardo_lieve":
        return _fmt_dt(ora_a + timedelta(minutes=random.randint(15, 38)))
    if category == "ritardo_grave":
        return _fmt_dt(ora_a + timedelta(minutes=random.randint(55, 120)))
    return _fmt_dt(ora_a)


def generate(seed: int = 42) -> list[Path]:
    random.seed(seed)
    fieldnames, base_rows = _read_rows(SRC)

    OUT.mkdir(parents=True, exist_ok=True)
    # pulisci snapshot precedenti
    for f in OUT.glob("tp_snapshot_*.csv"):
        f.unlink()
    # rimuovi current.csv vecchio
    cur = OUT / "current.csv"
    if cur.exists():
        cur.unlink()

    # T0 = stato attuale del CSV: copia identica
    t0 = datetime.now().replace(microsecond=0)
    timestamps = [t0 + timedelta(minutes=2 * i) for i in range(6)]

    snapshots: list[list[dict[str, str]]] = []
    # snapshot 0: copia base
    snapshots.append([dict(r) for r in base_rows])

    # working state
    work = [dict(r) for r in base_rows]
    # indici disponibili
    def _idx_by_stato(stato: str, work_rows: list[dict[str, str]]) -> list[int]:
        return [i for i, r in enumerate(work_rows)
                if (r.get("Tipo Riga") or "").strip() == "1"
                and (r.get("Stato") or "").strip() == stato]

    for step in range(1, 6):
        ts = timestamps[step]
        mod_str = _fmt_dt(ts)

        # Programmate → Evase
        prog_idx = _idx_by_stato("Programmata", work)
        random.shuffle(prog_idx)
        to_evade = prog_idx[:PROG_TO_EVASA_PER_STEP]
        for i in to_evade:
            r = work[i]
            cat = random.choices(
                ["in_fascia", "ritardo_lieve", "ritardo_grave"],
                weights=[0.6, 0.25, 0.15],
            )[0]
            r["Stato"] = "Evasa"
            r["Data Cons. Reale"] = _make_real_for(r, cat)
            r["Modifica"] = mod_str
            r["Causale"] = "Esito ok"
        prog_idx = [i for i in prog_idx if i not in to_evade]

        # Programmate → Fallite
        n_fall = random.randint(*PROG_TO_FALLITA_PER_STEP)
        to_fail = prog_idx[:n_fall]
        for i in to_fail:
            r = work[i]
            r["Stato"] = "Fallita"
            r["Causale"] = random.choice(CAUSALI)
            r["Modifica"] = mod_str
        prog_idx = prog_idx[n_fall:]

        # Programmate → Respinta
        to_refuse = prog_idx[:PROG_TO_RESPINTA_PER_STEP]
        for i in to_refuse:
            r = work[i]
            r["Stato"] = "Respinta"
            r["Causale"] = "Cliente rifiuta consegna"
            r["Modifica"] = mod_str

        # Lavorazione → Evasa
        lav_idx = _idx_by_stato("Lavorazione", work)
        random.shuffle(lav_idx)
        for i in lav_idx[:LAV_TO_EVASA_PER_STEP]:
            r = work[i]
            cat = random.choice(["in_fascia", "ritardo_lieve"])
            r["Stato"] = "Evasa"
            r["Data Cons. Reale"] = _make_real_for(r, cat)
            r["Modifica"] = mod_str
            r["Causale"] = "Esito ok"

        snapshots.append([dict(r) for r in work])

    # scrivi i 6 file
    paths: list[Path] = []
    for i, snap in enumerate(snapshots):
        name = f"tp_snapshot_{timestamps[i].strftime('%H%M%S')}.csv"
        p = OUT / name
        _write_rows(p, fieldnames, snap)
        paths.append(p)

    print(f"Generated {len(paths)} snapshots in {OUT}")
    for p in paths:
        print(f"  - {p.name} ({p.stat().st_size:,} bytes)")
    return paths


if __name__ == "__main__":
    if not SRC.exists():
        print(f"ERROR: source CSV missing: {SRC}", file=sys.stderr)
        sys.exit(1)
    generate()
