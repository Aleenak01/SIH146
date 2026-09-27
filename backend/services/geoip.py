"""
Offline GeoIP lookups: IP address -> country and autonomous system (ASN), from two DB-IP Lite MMDB files.

    IP geolocation by DB-IP.com (https://db-ip.com), licensed CC BY 4.0. Files: data/geoip/dbip-country-lite.mmdb and
    data/geoip/dbip-asn-lite.mmdb (see data/geoip/ATTRIBUTION.txt).

The files are opened lazily and only once. `lookup` never raises: if a file is missing or unreadable, or the address is
private / reserved / not in the database, the corresponding fields are None. `status()` says what is available.

A lookup describes where an IP address range is registered. In this project the IP addresses in rich transaction records are
SYNTHETIC values sampled from public ranges; a lookup result is therefore a property of the range, never evidence that any
real person or network did anything.
"""

from __future__ import annotations

import ipaddress
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, Settings

ATTRIBUTION = "IP geolocation by DB-IP.com"
LICENSE = "CC BY 4.0"
SOURCE_URL = "https://db-ip.com"
EMPTY = {"country_code": None, "country_name": None, "asn": None, "asn_org": None}


@dataclass
class _Db:
    path: Path
    kind: str                               # 'country' or 'asn'
    reader: Any = None
    error: str | None = None


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


class GeoIP:
    """`GeoIP(country_path, asn_path)` for the real files, or `GeoIP(country_reader=..., asn_reader=...)` to inject stand-ins in tests."""

    def __init__(self, country_path: str | Path | None = None, asn_path: str | Path | None = None, *, country_reader: Any = None, asn_reader: Any = None) -> None:
        self._country = _Db(Path(country_path) if country_path else Path("(injected)"), "country", country_reader)
        self._asn = _Db(Path(asn_path) if asn_path else Path("(injected)"), "asn", asn_reader)
        self._opened = country_reader is not None or asn_reader is not None or not (country_path or asn_path)
        self._lock = threading.Lock()
        self._cached = lru_cache(maxsize=65536)(self._lookup_uncached)

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeoIP":
        return cls(settings.geoip_country_db, settings.geoip_asn_db)

    # ---- opening ---------------------------------------------------------------------------------------------
    def _open(self) -> None:
        with self._lock:
            if self._opened:
                return
            for db in (self._country, self._asn):
                if db.reader is not None:
                    continue
                if not db.path.is_file():
                    db.error = "file not found"
                    continue
                try:
                    import maxminddb                     # imported here so the app runs without the package until GeoIP is used

                    db.reader = maxminddb.open_database(str(db.path))
                except Exception as e:                  # missing package, corrupt file, ...
                    db.error = f"{type(e).__name__}: {e}"[:200]
            self._opened = True

    @property
    def available(self) -> bool:
        """True if at least one of the two databases could be opened."""
        self._open()
        return self._country.reader is not None or self._asn.reader is not None

    # ---- lookups ---------------------------------------------------------------------------------------------
    def lookup(self, ip: str | None) -> dict[str, Any]:
        """{country_code, country_name, asn, asn_org}; a field is None when unknown. Never raises."""
        if not ip:
            return dict(EMPTY)
        try:
            return dict(self._cached(str(ip).strip()))
        except Exception:
            return dict(EMPTY)

    def _lookup_uncached(self, ip: str) -> dict[str, Any]:
        out = dict(EMPTY)
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return out
        if not addr.is_global:                          # private, loopback, documentation, reserved: not in the databases
            return out
        self._open()
        if self._country.reader is not None:
            try:
                rec = self._country.reader.get(str(addr)) or {}
                country = rec.get("country") or {}
                out["country_code"] = country.get("iso_code")
                out["country_name"] = (country.get("names") or {}).get("en")
            except Exception:
                pass
        if self._asn.reader is not None:
            try:
                rec = self._asn.reader.get(str(addr)) or {}
                out["asn"] = rec.get("autonomous_system_number")
                out["asn_org"] = rec.get("autonomous_system_organization")
            except Exception:
                pass
        return out

    # ---- status ------------------------------------------------------------------------------------------------
    def _db_status(self, db: _Db) -> dict[str, Any]:
        info: dict[str, Any] = {"kind": db.kind, "file": _rel(db.path) if db.path.name != "(injected)" else "(injected)",
                                "opened": db.reader is not None, "error": db.error}
        if db.path.is_file():
            info["size_bytes"] = db.path.stat().st_size
        if db.reader is not None:
            try:
                meta = db.reader.metadata()
                info["database_type"] = getattr(meta, "database_type", None)
                info["build_date"] = datetime.fromtimestamp(meta.build_epoch, tz=timezone.utc).date().isoformat()
            except Exception:
                pass
        return info

    def status(self) -> dict[str, Any]:
        self._open()
        dbs = [self._db_status(self._country), self._db_status(self._asn)]
        ok = sum(d["opened"] for d in dbs)
        message = ("GeoIP lookups are available (country and ASN)." if ok == 2 else
                   "GeoIP is partly available: " + "; ".join(f"{d['kind']}: {d['error'] or 'not opened'}" for d in dbs if not d["opened"]) if ok == 1 else
                   "GeoIP is not available (database files not found or unreadable); lookups return empty values.")
        return {"available": ok > 0, "complete": ok == 2, "message": message, "databases": dbs, "attribution": ATTRIBUTION,
                "license": LICENSE, "source": SOURCE_URL,
                "note": "Country/ASN describe where an IP range is registered. The IP addresses in this project's rich records are synthetic."}

    def close(self) -> None:
        for db in (self._country, self._asn):
            if db.reader is not None and hasattr(db.reader, "close"):
                try:
                    db.reader.close()
                except Exception:
                    pass
            db.reader = None
