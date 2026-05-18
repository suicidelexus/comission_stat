from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .hybrid_client import Account, Advertiser, HybridAuthError, make_client
from .models import CampaignPaceOut, SignalLevel
from .pacing import compute_pace

log = logging.getLogger("dsp_pacing")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx логирует каждый запрос на INFO — выключаем шум
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Сессия Hybrid stateful — параллельные sync'и ломают csid друг у друга.
# Lock держит одновременно только один sync. Остальные ждут.
_sync_lock = asyncio.Lock()
# Простой in-memory кэш последнего успешного sync'а
_cache: dict[str, list[CampaignPaceOut]] = {"items": []}
_cache_ts: dict[str, Optional[str]] = {"at": None}

# Прогресс текущего/последнего синка — отдаётся в /sync/status для прогресс-бара.
_sync_progress: dict = {
    "status": "idle",          # idle | running | done | error
    "current": 0,
    "total": 0,
    "current_agency": "",
    "agencies_done": 0,
    "campaigns_collected": 0,
    "started_at": None,
    "finished_at": None,
    "elapsed_seconds": 0,
    "error": None,
}
_sync_task: Optional[asyncio.Task] = None


_CACHE_FILE = Path(__file__).resolve().parent.parent / "cache.json"


def _load_cache_from_disk() -> None:
    if not _CACHE_FILE.exists():
        return
    try:
        import json
        raw = json.loads(_CACHE_FILE.read_text())
        if isinstance(raw, dict):
            items_raw = raw.get("items") or []
            _cache_ts["at"] = raw.get("at")
        else:
            items_raw = raw
        _cache["items"] = [CampaignPaceOut.model_validate(x) for x in items_raw]
        log.info("loaded %d campaigns from disk cache", len(_cache["items"]))
    except Exception as e:
        log.warning("disk cache load failed: %s", e)


def _save_cache_to_disk() -> None:
    try:
        import json
        payload = {
            "at": _cache_ts.get("at"),
            "items": [x.model_dump(mode="json") for x in _cache["items"]],
        }
        _CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        log.warning("disk cache save failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_cache_from_disk()
    app.state.hybrid = make_client()
    try:
        yield
    finally:
        _save_cache_to_disk()
        await app.state.hybrid.close()


app = FastAPI(
    title="DSP Pacing",
    description="Pace-мониторинг кампаний в Hybrid DSP",
    version="0.2.0",
    lifespan=lifespan,
)


async def _resolve_agencies() -> list[Account]:
    """Возвращает список агентств, по которым работаем."""
    client = app.state.hybrid
    try:
        all_agencies = await client.list_agencies()
    except HybridAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))

    wanted = set(settings.agency_ids)
    if not wanted:
        return all_agencies
    return [a for a in all_agencies if a.id in wanted]


async def _fetch_all_campaigns() -> list[CampaignPaceOut]:
    """Stateful обход: agency → switch → advertisers → for each → GetTotal → pace.
    Под глобальным lock'ом — параллельные вызовы ломают session csid в Hybrid'е.
    """
    async with _sync_lock:
        return await _fetch_all_campaigns_locked()


async def _fetch_all_campaigns_locked() -> list[CampaignPaceOut]:
    client = app.state.hybrid
    end = date.today()
    start = end.replace(day=1)
    tomorrow = end.toordinal() + 1

    agencies = await _resolve_agencies()
    out: list[CampaignPaceOut] = []
    dropped = {"not_started": 0, "finished": 0, "no_limit": 0, "paused": 0, "no_fact_today": 0, "ends_today": 0}

    _sync_progress.update(total=len(agencies), current=0, current_agency="", agencies_done=0, campaigns_collected=0)

    for ai, agency in enumerate(agencies, 1):
        agency_label = agency.name or agency.id
        _sync_progress.update(current=ai, current_agency=agency_label)
        log.info("[%d/%d] agency %s (%s) — switching", ai, len(agencies), agency_label, agency.id)
        try:
            await client.switch_to_agency(agency.id)
            advertisers = await client.list_advertisers()
        except HybridAuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        except Exception as e:
            log.warning("agency %s skipped: %s", agency.id, e)
            _sync_progress["agencies_done"] = ai
            continue

        # Префильтр только по имени — мусорные advertiser'ы (for deleting/archive/test).
        # Balance НЕ фильтруем: деньги могли быть перенесены с advertiser-баланса
        # на кампанию, и при balance=0 у advertiser'а могут быть активные кампании.
        skip_re = re.compile(settings.skip_advertiser_name_regex) if settings.skip_advertiser_name_regex else None
        before = len(advertisers)
        if skip_re:
            advertisers = [a for a in advertisers if not skip_re.search(a.name or "")]
        log.info("  advertisers: %d → %d (after name filter)", before, len(advertisers))

        # GetTotal'ы можно тащить параллельно — context agency уже зафиксирован,
        # стейт сессии больше не меняется. Семафор бережёт API от перегруза.
        sem = asyncio.Semaphore(settings.fetch_concurrency)

        async def fetch_one(adv: Advertiser) -> list[CampaignPaceOut]:
            async with sem:
                try:
                    resp = await client.get_total(adv.id, start, end)
                except Exception as e:
                    log.warning("    adv %s (%s) skipped: %s", adv.name, adv.id, e)
                    return []
            return [
                compute_pace(
                    agency=agency.name or agency.id,
                    advertiser_id=adv.id,
                    advertiser_name=adv.name or "",
                    currency=adv.currency,
                    c=c,
                    period_start=start,
                    period_end=end,
                )
                for c in resp.campaigns
            ]

        results = await asyncio.gather(*(fetch_one(a) for a in advertisers))
        for r in results:
            for p in r:
                if p.status != 1:
                    dropped["paused"] += 1
                    continue
                if p.signal == SignalLevel.NOT_STARTED:
                    dropped["not_started"] += 1
                    continue
                if p.signal == SignalLevel.FINISHED:
                    dropped["finished"] += 1
                    continue
                if p.signal == SignalLevel.NO_LIMIT:
                    dropped["no_limit"] += 1
                    log.info("    no_limit: %s/%s (%s)", agency_label, p.campaign_name, p.campaign_id)
                    continue
                if p.today_fact <= 0:
                    dropped["no_fact_today"] += 1
                    continue
                # завтра ещё крутит?
                if not p.is_dont_expire:
                    if p.end_date is None or p.end_date.toordinal() < tomorrow:
                        dropped["ends_today"] += 1
                        continue
                out.append(p)
        _sync_progress.update(agencies_done=ai, campaigns_collected=len(out))

    log.info(
        "done: %d active campaigns kept; dropped: %s",
        len(out),
        ", ".join(f"{k}={v}" for k, v in dropped.items()),
    )
    _cache["items"] = out
    _cache_ts["at"] = datetime.utcnow().isoformat()
    _save_cache_to_disk()
    return out


# ---------- endpoints ----------


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/agencies", response_model=list[Account])
async def agencies(only_named: bool = Query(True, description="скрыть агентства без имени")):
    """Список агентств доступных текущим кукам. Удобно чтоб выбрать какие
    положить в HYBRID_AGENCY_IDS."""
    accs = await _resolve_agencies() if settings.agency_ids else await app.state.hybrid.list_agencies()
    if only_named:
        accs = [a for a in accs if a.name.strip()]
    return accs


@app.get("/advertisers")
async def advertisers_of(agency_id: str = Query(..., description="agency_id")):
    """Список рекламодателей конкретного агентства."""
    client = app.state.hybrid
    try:
        await client.switch_to_agency(agency_id)
        advs = await client.list_advertisers()
    except HybridAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return advs


@app.get("/campaigns", response_model=list[CampaignPaceOut])
async def list_campaigns(
    signal: Optional[SignalLevel] = Query(None),
    only_active: bool = Query(True, description="только status=1"),
):
    items = await _fetch_all_campaigns()
    if only_active:
        items = [x for x in items if x.status == 1]
    if signal:
        items = [x for x in items if x.signal == signal]
    order = {
        SignalLevel.GREEN: 0,
        SignalLevel.YELLOW: 1,
        SignalLevel.RED: 2,
        SignalLevel.NO_LIMIT: 3,
        SignalLevel.NOT_STARTED: 4,
        SignalLevel.FINISHED: 5,
    }
    items.sort(key=lambda x: (order.get(x.signal, 9), -(x.pace_today or 0)))
    return items


@app.get("/campaigns/green", response_model=list[CampaignPaceOut])
async def list_green():
    items = await _fetch_all_campaigns()
    return [x for x in items if x.signal == SignalLevel.GREEN]


async def _run_sync_background() -> None:
    """Обёртка для фонового запуска синка — обновляет _sync_progress."""
    started = time.monotonic()
    _sync_progress.update(
        status="running",
        started_at=datetime.utcnow().isoformat(),
        finished_at=None,
        error=None,
        current=0,
        total=0,
        agencies_done=0,
        campaigns_collected=0,
        current_agency="",
        elapsed_seconds=0,
    )
    try:
        await _fetch_all_campaigns()
        _sync_progress["status"] = "done"
    except Exception as e:
        log.exception("background sync failed")
        _sync_progress["status"] = "error"
        _sync_progress["error"] = str(e)
    finally:
        _sync_progress["finished_at"] = datetime.utcnow().isoformat()
        _sync_progress["elapsed_seconds"] = round(time.monotonic() - started, 1)


@app.post("/sync/start")
async def sync_start() -> dict:
    """Запускает синк в фоне. Если уже идёт — возвращает текущий статус."""
    global _sync_task
    if _sync_task and not _sync_task.done():
        return {"started": False, "reason": "already_running", "progress": _sync_progress}
    _sync_task = asyncio.create_task(_run_sync_background())
    return {"started": True, "progress": _sync_progress}


@app.get("/sync/status")
async def sync_status() -> dict:
    out = dict(_sync_progress)
    if out["status"] == "running" and out.get("started_at"):
        started_dt = datetime.fromisoformat(out["started_at"])
        out["elapsed_seconds"] = round((datetime.utcnow() - started_dt).total_seconds(), 1)
    return out


@app.get("/summary")
async def summary() -> dict:
    items = await _fetch_all_campaigns()
    by_signal: dict[str, int] = {}
    for x in items:
        by_signal[x.signal.value] = by_signal.get(x.signal.value, 0) + 1
    return {
        "total": len(items),
        "by_signal": by_signal,
        "agencies_count": len({x.agency for x in items}),
        "advertisers_count": len({x.advertiser_id for x in items}),
        "cached_at_utc": _cache_ts["at"],
    }


_VISIBLE_SIGNALS = {SignalLevel.GREEN, SignalLevel.YELLOW, SignalLevel.RED}


def _visible_items() -> list[CampaignPaceOut]:
    return [x for x in _cache["items"] if x.signal in _VISIBLE_SIGNALS and x.status == 1]


@app.get("/cache/summary")
async def cache_summary() -> dict:
    """Сводка по кэшу — не дёргает Hybrid. Учитывает только активные кампании
    с сигналом green/yellow/red (после фильтрации finished/not_started/no_limit)."""
    items = _visible_items()
    by_signal: dict[str, int] = {}
    for x in items:
        by_signal[x.signal.value] = by_signal.get(x.signal.value, 0) + 1
    return {
        "total": len(items),
        "by_signal": by_signal,
        "cached_at_utc": _cache_ts["at"],
    }


@app.get("/cache/green", response_model=list[CampaignPaceOut])
async def cache_green():
    """Зелёные из кэша — мгновенно."""
    return [x for x in _visible_items() if x.signal == SignalLevel.GREEN]


@app.get("/cache/all", response_model=list[CampaignPaceOut])
async def cache_all(
    signal: Optional[SignalLevel] = Query(None),
):
    """Всё из кэша — мгновенно. Используется HTML-страничкой.
    Возвращаются только активные кампании с сигналом green/yellow/red."""
    items = _visible_items()
    if signal:
        items = [x for x in items if x.signal == signal]
    return items


# HTML страничка
_static_dir = Path(__file__).resolve().parent / "static"


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(_static_dir / "index.html")


app.mount("/static", StaticFiles(directory=_static_dir), name="static")
