# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 stepspower
"""Tesla charging history (Supercharger sessions) and invoice PDFs via the Fleet API.

Endpoints (developer.tesla.com, Charging Endpoints; scope ``vehicle_charging_cmds``; the
docs list no pricing category for them, i.e. "This endpoint is not charged"):

- ``GET /api/1/dx/charging/history`` - paginated (``pageNo``/``pageSize``), wrapped by
  ``tesla_fleet_api``'s ``Charging.history()``, which is reused here.
- ``GET /api/1/dx/charging/invoice/{contentId}`` - returns the raw PDF. Not wrapped by the
  library (its request helper only accepts JSON), so it is fetched directly here with the
  same server and bearer token.

``/api/1/dx/charging/sessions`` is for business fleet accounts only and is not used.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import secrets
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from socks.fleet import oauth
from socks.fleet.oauth import Endpoints, FleetSettings, OAuthError, _with_session

if TYPE_CHECKING:
    import aiohttp

log = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_PAGES = 200  # safety stop: 10 000 sessions
MAX_PDF_BYTES = 20 * 1024 * 1024
INVOICE_PATH = "api/1/dx/charging/invoice/"


class ChargingError(Exception):
    """Charging history/invoice failure. Never contains tokens."""


@dataclass
class RequestStats:
    """Counts Fleet API requests so the CLI can report them."""

    history_pages: int = 0
    invoices: int = 0

    @property
    def total(self) -> int:
        return self.history_pages + self.invoices


@dataclass(frozen=True)
class ChargingSession:
    session_id: str
    start: datetime | None
    end: datetime | None
    site: str
    energy_kwh: float | None
    cost: float | None
    currency: str
    vin: str
    invoice_ids: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


# --------------------------------------------------------------------------- parsing


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_session(item: dict[str, Any]) -> ChargingSession:
    fees = [f for f in item.get("fees") or [] if isinstance(f, dict)]
    energy: float | None = None
    cost: float | None = None
    currency = ""
    for fee in fees:
        currency = currency or str(fee.get("currencyCode") or "")
        due = _num(fee.get("totalDue"))
        if due is not None:
            cost = (cost or 0.0) + due
        if fee.get("feeType") == "CHARGING" and str(fee.get("uom", "")).lower() == "kwh":
            used = _num(fee.get("usageBase"))
            if used is not None:
                energy = (energy or 0.0) + used
    invoices = tuple(
        str(inv["contentId"])
        for inv in item.get("invoices") or []
        if isinstance(inv, dict) and inv.get("contentId")
    )
    return ChargingSession(
        session_id=str(item.get("sessionId", "")),
        start=_dt(item.get("chargeStartDateTime")),
        end=_dt(item.get("chargeStopDateTime")),
        site=str(item.get("siteLocationName") or ""),
        energy_kwh=energy,
        cost=round(cost, 2) if cost is not None else None,
        currency=currency,
        vin=str(item.get("vin") or ""),
        invoice_ids=invoices,
        raw=item,
    )


def _page_items(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Return (items, totalResults) from one history page, tolerating both nestings."""
    if not isinstance(payload, dict):
        raise ChargingError("unexpected charging history response")
    resp = payload.get("response", payload)
    total = payload.get("totalResults")
    if isinstance(resp, dict):
        total = resp.get("totalResults", total)
        items = resp.get("data", [])
    else:
        items = resp
    if not isinstance(items, list):
        raise ChargingError("unexpected charging history response")
    return [i for i in items if isinstance(i, dict)], total if isinstance(total, int) else None


# --------------------------------------------------------------------------- API


def _server(settings: FleetSettings, endpoints: Endpoints | None) -> str:
    return (endpoints or Endpoints.for_region(settings.region)).audience


def _token_provider(
    settings: FleetSettings, endpoints: Endpoints | None, token_path: Path | None
) -> Any:
    async def provider() -> str:
        return await oauth.access_token(settings, endpoints=endpoints, path=token_path)

    return provider


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


async def charging_history(
    settings: FleetSettings,
    *,
    since: date | None = None,
    until: date | None = None,
    endpoints: Endpoints | None = None,
    token_path: Path | None = None,
    session: aiohttp.ClientSession | None = None,
    stats: RequestStats | None = None,
    page_size: int = PAGE_SIZE,
) -> list[ChargingSession]:
    """All charging sessions in [since, until] (inclusive dates, UTC), oldest first."""
    from tesla_fleet_api import TeslaFleetApi

    stats = stats if stats is not None else RequestStats()
    vin = getattr(settings, "vin", "") or None
    start_s = _day_start(since).isoformat() if since else None
    end_s = _day_start(until + timedelta(days=1)).isoformat() if until else None

    async def _go(s: aiohttp.ClientSession) -> list[ChargingSession]:
        api = TeslaFleetApi(
            s,
            access_token=_token_provider(settings, endpoints, token_path),
            server=_server(settings, endpoints),
            energy_scope=False,
            partner_scope=False,
            user_scope=False,
            vehicle_scope=False,
        )
        out: list[ChargingSession] = []
        for page in range(1, MAX_PAGES + 1):
            stats.history_pages += 1
            try:
                payload = await api.charging.history(
                    vin=vin,
                    startTime=start_s,
                    endTime=end_s,
                    pageNo=page,
                    pageSize=page_size,
                    sortBy="chargeStartDateTime",
                    sortOrder="ASC",
                )
            except OAuthError:
                raise
            except Exception as exc:  # library raises typed errors per HTTP status
                raise ChargingError(f"charging history failed: {type(exc).__name__}") from None
            items, total = _page_items(payload)
            out.extend(parse_session(i) for i in items)
            if len(items) < page_size or (total is not None and len(out) >= total):
                break
        return out

    sessions: list[ChargingSession] = await _with_session(session, _go)
    # the server filters by time already; filter again in case it ignores the params
    lo = _day_start(since) if since else None
    hi = _day_start(until + timedelta(days=1)) if until else None
    result = [
        x
        for x in sessions
        if x.start is None or ((lo is None or x.start >= lo) and (hi is None or x.start < hi))
    ]
    seen: set[str] = set()
    unique = [x for x in result if not (x.session_id in seen or seen.add(x.session_id))]
    unique.sort(key=lambda x: x.start or datetime.min.replace(tzinfo=UTC))
    return unique


# --------------------------------------------------------------------------- files


def slug(text: str, max_len: int = 40, fallback: str = "unknown") -> str:
    """Filesystem-safe ASCII slug: [a-z0-9-] only, so no separators, dots or traversal."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:max_len].strip("-")
    return s or fallback


def invoice_filename(sess: ChargingSession, content_id: str) -> str:
    day = sess.start.date().isoformat() if sess.start else "0000-00-00"
    return f"{day}_{slug(sess.site)}_{slug(content_id, max_len=64)}.pdf"


def _ensure_dir(path: Path) -> None:
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
        os.chmod(path, 0o700)
    elif not path.is_dir():
        raise ChargingError(f"{path} is not a directory")


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)


async def _fetch_invoice(
    s: aiohttp.ClientSession, server: str, token: str, content_id: str
) -> bytes:
    url = f"{server.rstrip('/')}/{INVOICE_PATH}{quote(content_id, safe='')}"
    async with s.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/pdf"},
        allow_redirects=False,
    ) as resp:
        if resp.status != 200:
            raise ChargingError(f"invoice {content_id[:64]}: HTTP {resp.status}")
        body = await resp.content.read(MAX_PDF_BYTES + 1)
    if len(body) > MAX_PDF_BYTES:
        raise ChargingError(f"invoice {content_id[:64]}: larger than {MAX_PDF_BYTES} bytes")
    if not body.startswith(b"%PDF-"):
        raise ChargingError(f"invoice {content_id[:64]}: response is not a PDF")
    return body


async def download_invoices(
    settings: FleetSettings,
    sessions: Iterable[ChargingSession],
    dest_dir: Path,
    *,
    overwrite: bool = False,
    endpoints: Endpoints | None = None,
    token_path: Path | None = None,
    session: aiohttp.ClientSession | None = None,
    stats: RequestStats | None = None,
) -> list[Path]:
    """Download every invoice PDF not yet present. Returns only newly written files."""
    stats = stats if stats is not None else RequestStats()
    dest_dir = Path(dest_dir).expanduser()
    _ensure_dir(dest_dir)
    root = dest_dir.resolve()
    todo: list[tuple[Path, str]] = []
    for sess in sessions:
        for cid in sess.invoice_ids:
            path = dest_dir / invoice_filename(sess, cid)
            if path.resolve().parent != root:  # belt and braces; slug() already prevents it
                raise ChargingError("refusing to write outside the download directory")
            if path.exists() and not overwrite:
                log.debug("skip existing %s", path.name)
                continue
            todo.append((path, cid))
    if not todo:
        return []
    server = _server(settings, endpoints)

    async def _go(s: aiohttp.ClientSession) -> list[Path]:
        written: list[Path] = []
        for path, cid in todo:
            token = await oauth.access_token(settings, endpoints=endpoints, path=token_path)
            stats.invoices += 1
            data = await _fetch_invoice(s, server, token, cid)
            _write_private(path, data)
            written.append(path)
        return written

    return await _with_session(session, _go)


_CSV_FIELDS = (
    "session_id",
    "start",
    "end",
    "site",
    "energy_kwh",
    "cost",
    "currency",
    "vin",
    "invoice_ids",
)


def _csv_safe(value: str) -> str:
    # neutralise spreadsheet formula injection from server-provided text (e.g. site names)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def write_summary_csv(sessions: Sequence[ChargingSession], path: Path) -> Path:
    """Write one row per session (0600)."""
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_CSV_FIELDS)
    for x in sessions:
        w.writerow(
            [
                _csv_safe(x.session_id),
                x.start.isoformat() if x.start else "",
                x.end.isoformat() if x.end else "",
                _csv_safe(x.site),
                "" if x.energy_kwh is None else f"{x.energy_kwh:g}",
                "" if x.cost is None else f"{x.cost:.2f}",
                _csv_safe(x.currency),
                _csv_safe(x.vin),
                _csv_safe(";".join(x.invoice_ids)),
            ]
        )
    path = Path(path).expanduser()
    _ensure_dir(path.parent)
    _write_private(path, buf.getvalue().encode("utf-8"))
    return path
