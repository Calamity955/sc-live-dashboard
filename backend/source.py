"""Input layer astratto.

Strategy pattern per le sorgenti dati. Switch a runtime via env `DATA_SOURCE`.

- `local`  : cartella locale (sviluppo/test/fallback)
- `sftp`   : server SFTP remoto (produzione con IT)
"""
from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .models import Delivery
from .parser import parse_csv, parse_csv_bytes

log = logging.getLogger("sc-dashboard")


class Source(ABC):
    """Strategy interface per acquisire snapshot CSV."""

    @abstractmethod
    async def current_mtime(self) -> float | None:
        """Mtime/identificativo univoco dello snapshot remoto corrente."""

    @abstractmethod
    async def load(self) -> list[Delivery]:
        """Carica e parsa il file corrente."""


# ---------------------------------------------------------------------------
# Local folder source
# ---------------------------------------------------------------------------

class LocalFolderSource(Source):
    """Sorgente locale: monitora `<data_path>/current.csv` via mtime."""

    def __init__(self, data_path: str | Path, filename: str = "current.csv"):
        self.data_path = Path(data_path)
        self.filename = filename
        self.data_path.mkdir(parents=True, exist_ok=True)

    @property
    def file_path(self) -> Path:
        return self.data_path / self.filename

    async def current_mtime(self) -> float | None:
        try:
            return self.file_path.stat().st_mtime
        except FileNotFoundError:
            return None

    async def load(self) -> list[Delivery]:
        path = self.file_path
        if not path.exists():
            return []
        return await asyncio.to_thread(parse_csv, path)


# ---------------------------------------------------------------------------
# SFTP source — produzione (IT deposita CSV ogni 2 minuti)
# ---------------------------------------------------------------------------

class SftpSource(Source):
    """Sorgente SFTP.

    Strategie supportate (`SFTP_MODE`):
    - `single`  (default): legge un file fisso `SFTP_PATH` (es. `consegne.csv`)
    - `newest`           : scarica il file più recente in `SFTP_PATH` che matcha `SFTP_PATTERN`

    Supporta automaticamente file `.gz` (gzip): vengono decompressi al volo.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str | None = None,
        key_path: str | None = None,
        port: int = 22,
        remote_path: str = "/consegne.csv",
        mode: str = "single",
        pattern: str = "*.csv*",
        known_hosts: str | None = None,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_path = key_path
        self.remote_path = remote_path
        self.mode = mode  # "single" | "newest"
        self.pattern = pattern
        self.known_hosts = known_hosts  # None = accetta qualsiasi host (LAN), path file = strict

        self._last_file_meta: tuple[str, float] | None = None  # (filename, mtime)

    async def _connect(self):
        """Apre connessione SFTP (lazy import per non pesare se non si usa)."""
        import asyncssh  # type: ignore
        opts: dict = {
            "host": self.host,
            "port": self.port,
            "username": self.user,
        }
        if self.password:
            opts["password"] = self.password
        if self.key_path:
            opts["client_keys"] = [self.key_path]
        # known_hosts=None disabilita la verifica (per LAN IT spesso necessario)
        opts["known_hosts"] = self.known_hosts
        return await asyncssh.connect(**opts)

    async def _resolve_target(self, sftp) -> tuple[str, float] | None:
        """Trova il file da leggere; restituisce (path, mtime) o None."""
        if self.mode == "single":
            try:
                attrs = await sftp.stat(self.remote_path)
                return self.remote_path, float(attrs.mtime or 0)
            except Exception as exc:
                log.warning("SFTP stat %s failed: %s", self.remote_path, exc)
                return None
        elif self.mode == "newest":
            try:
                entries = await sftp.glob(f"{self.remote_path.rstrip('/')}/{self.pattern}")
            except Exception as exc:
                log.warning("SFTP glob failed: %s", exc)
                return None
            if not entries:
                return None
            # ordina per mtime (più recente in cima)
            scored: list[tuple[float, str]] = []
            for p in entries:
                try:
                    a = await sftp.stat(p)
                    scored.append((float(a.mtime or 0), p))
                except Exception:
                    continue
            if not scored:
                return None
            scored.sort(reverse=True)
            return scored[0][1], scored[0][0]
        else:
            raise ValueError(f"SFTP_MODE non valido: {self.mode}")

    async def current_mtime(self) -> float | None:
        try:
            async with await self._connect() as conn:
                async with await conn.start_sftp_client() as sftp:
                    res = await self._resolve_target(sftp)
                    if res is None:
                        return None
                    path, mtime = res
                    self._last_file_meta = (path, mtime)
                    return mtime
        except Exception as exc:
            log.warning("SFTP current_mtime error: %s", exc)
            return None

    async def load(self) -> list[Delivery]:
        try:
            async with await self._connect() as conn:
                async with await conn.start_sftp_client() as sftp:
                    res = await self._resolve_target(sftp)
                    if res is None:
                        return []
                    path, mtime = res
                    self._last_file_meta = (path, mtime)
                    # leggi il file in memoria
                    async with await sftp.open(path, "rb") as f:
                        data = await f.read()
                    # se gz, decomprimi
                    if path.endswith(".gz"):
                        data = gzip.decompress(data)
                    return parse_csv_bytes(data)
        except Exception as exc:
            log.warning("SFTP load error: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_source_from_env() -> Source:
    """Legge `DATA_SOURCE` da env e costruisce la strategia adeguata."""
    kind = os.getenv("DATA_SOURCE", "local").lower()
    if kind == "local":
        return LocalFolderSource(os.getenv("DATA_PATH", "./data/csv"))
    if kind == "sftp":
        return SftpSource(
            host=os.environ["SFTP_HOST"],
            port=int(os.getenv("SFTP_PORT", "22")),
            user=os.environ["SFTP_USER"],
            password=os.getenv("SFTP_PASSWORD") or None,
            key_path=os.getenv("SFTP_KEY_PATH") or None,
            remote_path=os.getenv("SFTP_PATH", "/consegne.csv"),
            mode=os.getenv("SFTP_MODE", "single"),
            pattern=os.getenv("SFTP_PATTERN", "*.csv*"),
            known_hosts=os.getenv("SFTP_KNOWN_HOSTS") or None,
        )
    raise ValueError(f"DATA_SOURCE sconosciuto: {kind}")
