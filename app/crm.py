"""CRM-интеграция: создание / изменение / удаление комиссий (техкост).

═══════════════════════════════════════════════════════════════════════
ИЗОЛИРОВАННЫЙ МОДУЛЬ. Чтобы полностью вырезать CRM из проекта:
  1. удалить этот файл (app/crm.py)
  2. убрать в app/main.py 2 строки с "crm" (импорт + include_router)
  3. убрать в app/static/index.html блоки между <!-- CRM:start --> и <!-- CRM:end -->
═══════════════════════════════════════════════════════════════════════

ДВА ПРЕДОХРАНИТЕЛЯ (app/config.py):
  CRM_ENABLED=false — модуль выключен, все endpoint'ы отдают 403.
  CRM_DRY_RUN=true  — включённый модуль только логирует, НЕ шлёт в CRM.

ИЗОЛЯЦИЯ ПО ДАННЫМ: сервис работает ТОЛЬКО со своим реестром
(commissions_registry.json) — комиссии, которые он сам создал. В CRM ходит
только точечно: создать конкретную, найти её id фильтром по target,
изменить/удалить по id. Никаких «выгрузить все комиссии CRM».

Уровни комиссии (определяются набором полей в CommissionEntity):
  agency     — AgencyId + TradingDeskId
  advertiser — AgencyId + AdvertiserId + TradingDeskId
  campaign   — AgencyId + AdvertiserId + FolderId + CampaignId + TradingDeskId
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import settings

log = logging.getLogger("dsp_pacing.crm")

router = APIRouter(prefix="/crm", tags=["crm"])

_LOG_FILE = Path(__file__).resolve().parent.parent / "commissions_log.json"
_REGISTRY_FILE = Path(__file__).resolve().parent.parent / "commissions_registry.json"

# на 401 безопасно ретраить: «не авторизован» = запрос точно не изменил данные
_RETRY_401_ATTEMPTS = 3
_RETRY_401_DELAYS = [1.0, 2.0, 4.0]


# ---------- модели ----------

class CommissionRequest(BaseModel):
    """Создание комиссии. level определяет на что вешаем техкост."""
    level: str = "campaign"          # campaign | advertiser | agency
    tenant: str                      # label кабинета — по нему берём TradingDeskId
    agency_id: str                   # id агентства (для Create)
    agency_inventory_id: str = ""    # inventoryId агентства (для фильтров резолва)
    advertiser_id: Optional[str] = None
    campaign_id: Optional[str] = None
    commission_scale: float          # размер техкоста (%, целое)
    target_name: str = ""            # имя сущности — для лога/реестра


class UpdateRequest(BaseModel):
    commission_id: str
    commission_scale: float


class CommissionResult(BaseModel):
    ok: bool
    dry_run: bool
    message: str
    commission_id: Optional[str] = None
    crm_status: Optional[int] = None


# ---------- реестр (наши комиссии) ----------

def _load_registry() -> list[dict]:
    if not _REGISTRY_FILE.exists():
        return []
    try:
        return json.loads(_REGISTRY_FILE.read_text())
    except Exception:
        return []


def _save_registry(rows: list[dict]) -> None:
    try:
        _REGISTRY_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning("registry write failed: %s", e)


def _registry_upsert(entry: dict) -> None:
    """Добавить/обновить запись по commission_id."""
    rows = _load_registry()
    cid = entry.get("commission_id")
    for i, r in enumerate(rows):
        if cid and r.get("commission_id") == cid:
            rows[i] = {**r, **entry}
            _save_registry(rows)
            return
    rows.append(entry)
    _save_registry(rows)


def _registry_mark_removed(commission_id: str) -> None:
    rows = _load_registry()
    for r in rows:
        if r.get("commission_id") == commission_id:
            r["removed"] = True
            r["updated_at"] = datetime.utcnow().isoformat()
    _save_registry(rows)


def _append_log(entry: dict) -> None:
    try:
        data: list = []
        if _LOG_FILE.exists():
            data = json.loads(_LOG_FILE.read_text())
        data.append(entry)
        _LOG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning("commission log write failed: %s", e)


# ---------- HTTP к CRM ----------

def _crm_cookies() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in settings.crm_cookies.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _crm_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": f"https://{settings.crm_host}",
        "Referer": f"https://{settings.crm_host}/panel/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }


async def _crm_request(method: str, path: str, **kwargs) -> tuple[int, str]:
    """Запрос к CRM с ретраем на 401 (флап). 401 не меняет данные — повтор безопасен."""
    url = f"https://{settings.crm_host}{path}"
    async with httpx.AsyncClient(timeout=30.0, cookies=_crm_cookies()) as client:
        last: tuple[int, str] = (0, "")
        for attempt in range(_RETRY_401_ATTEMPTS):
            r = await client.request(method, url, headers=_crm_headers(), **kwargs)
            last = (r.status_code, r.text)
            if r.status_code != 401:
                return last
            if attempt < _RETRY_401_ATTEMPTS - 1:
                await asyncio.sleep(_RETRY_401_DELAYS[attempt])
                log.warning("CRM 401 (флап) — retry %d", attempt + 1)
        return last


def _td_id(tenant: str) -> str:
    td = settings.crm_td_map.get(tenant)
    if not td:
        raise HTTPException(
            status_code=400,
            detail=f"нет TradingDeskId для кабинета '{tenant}' — проверь CRM_TRADING_DESK_IDS",
        )
    return td


def _build_entity(req: CommissionRequest, td_id: str) -> dict:
    """CommissionEntity по уровню. Уровень = набор заполненных полей."""
    if req.level == "agency":
        return {"AgencyId": req.agency_id, "AdvertiserId": None, "TradingDeskId": td_id}
    if req.level == "advertiser":
        if not req.advertiser_id:
            raise HTTPException(status_code=400, detail="level=advertiser требует advertiser_id")
        return {
            "AgencyId": req.agency_id, "AdvertiserId": req.advertiser_id,
            "TradingDeskId": td_id,
        }
    # campaign
    if not req.campaign_id:
        raise HTTPException(status_code=400, detail="level=campaign требует campaign_id")
    return {
        "AgencyId": req.agency_id, "AdvertiserId": req.advertiser_id,
        "FolderId": None, "CampaignId": req.campaign_id, "TradingDeskId": td_id,
    }


def _target_id(req: CommissionRequest) -> str:
    """id сущности на которую вешаем комиссию (= entityId в ответе CRM)."""
    if req.level == "agency":
        return req.agency_id
    if req.level == "advertiser":
        return req.advertiser_id or ""
    return req.campaign_id or ""


async def _resolve_commission(req: CommissionRequest) -> Optional[dict]:
    """После Create — найти свою комиссию через CRM. Endpoint и матчинг зависят
    от уровня:
      campaign   — GetCampaignCommission, фильтр AgencyId(inventory)+AdvertiserId,
                   матч entityId == campaign_id
      advertiser — GetAdvertiserCommission, тот же фильтр, матч entityId == advertiser_id
      agency     — GetCommission (общий список), матч entityId == agency inventoryId
    Фильтры в этих endpoint'ах работают (в отличие от голого GetCommission)."""
    ce = {
        "IsRemoved": False, "EntityName": None, "EntitySortDirection": None,
        "TradingDeskId": None,
        "AgencyId": req.agency_inventory_id or None,
        "AdvertiserId": req.advertiser_id,
        "FolderId": None, "CampaignId": None,
    }
    body = {"Condition": {"Page": 0, "Limit": 50, "SortField": "createDate", "SortDirect": 0},
            "CommissionEntity": ce}

    if req.level == "campaign":
        endpoint, want = "GetCampaignCommission", req.campaign_id
    elif req.level == "advertiser":
        endpoint, want = "GetAdvertiserCommission", req.advertiser_id
    else:  # agency
        endpoint, want = "GetCommission", req.agency_inventory_id

    status, text = await _crm_request("POST", f"/core/AgencyCommission/{endpoint}", json=body)
    if status != 200:
        log.warning("resolve: %s → %d", endpoint, status)
        return None
    try:
        items = (json.loads(text) or {}).get("items") or []
    except Exception:
        return None
    # матч по entityId == id целевой сущности; не-removed; свежайшая
    cand = [it for it in items if it.get("entityId") == want and not it.get("isRemoved")]
    if not cand:
        return None
    cand.sort(key=lambda it: it.get("createDate", ""), reverse=True)
    top = cand[0]
    return {"id": top.get("id"), "overall_id": top.get("overallId"),
            "name": top.get("name"), "create_date": top.get("createDate")}


# ---------- endpoint'ы ----------

@router.get("/status")
async def crm_status() -> dict:
    return {
        "enabled": settings.crm_enabled,
        "dry_run": settings.crm_dry_run,
        "host": settings.crm_host,
        "trading_desks": settings.crm_td_map,
        "cookies_set": bool(settings.crm_cookies.strip()),
    }


@router.get("/commissions")
async def list_commissions() -> list[dict]:
    """Реестр наших комиссий (созданных через сервис). Из локального файла —
    в CRM за этим НЕ ходим."""
    return [r for r in _load_registry() if not r.get("removed")]


@router.post("/commission", response_model=CommissionResult)
async def create_commission(req: CommissionRequest) -> CommissionResult:
    if not settings.crm_enabled:
        raise HTTPException(status_code=403, detail="CRM-модуль выключен (CRM_ENABLED=false)")

    td_id = _td_id(req.tenant)
    scale = int(round(req.commission_scale))
    if scale <= 0 or scale > 100:
        raise HTTPException(status_code=400, detail="commission_scale должен быть в (0, 100]")

    entity = _build_entity(req, td_id)
    target_id = _target_id(req)
    payload = {
        "CommissionEntity": entity,
        "Commission": {
            "Id": None, "CommissionScale": scale,
            "DeviceType": None, "OS": None, "TrafficType": None,
            "BannerType": None, "Rewarded": None, "SspNumber": None,
        },
    }
    base = {
        "at": datetime.utcnow().isoformat(), "op": "create", "level": req.level,
        "tenant": req.tenant, "agency_id": req.agency_id,
        "advertiser_id": req.advertiser_id, "campaign_id": req.campaign_id,
        "target_id": target_id, "target_name": req.target_name,
        "trading_desk_id": td_id, "commission_scale": scale,
    }

    if settings.crm_dry_run:
        _append_log({**base, "mode": "dry_run", "payload": payload})
        return CommissionResult(
            ok=True, dry_run=True,
            message=f"DRY-RUN: комиссия {scale}% ({req.level}) НЕ отправлена. Залогировано.",
        )

    status, text = await _crm_request("POST", "/core/AgencyCommission/Create", json=payload)
    ok = 200 <= status < 300
    _append_log({**base, "mode": "live", "payload": payload,
                 "crm_status": status, "crm_response": text[:500], "ok": ok})
    if not ok:
        return CommissionResult(ok=False, dry_run=False, crm_status=status,
                                message=f"CRM вернул HTTP {status}")

    # Create id не возвращает — резолвим свежесозданную через GetXxxCommission
    resolved = await _resolve_commission(req)
    commission_id = resolved.get("id") if resolved else None
    _registry_upsert({
        "commission_id": commission_id or f"unknown-{int(datetime.utcnow().timestamp())}",
        "overall_id": resolved.get("overall_id") if resolved else None,
        "level": req.level, "tenant": req.tenant,
        "agency_id": req.agency_id, "agency_inventory_id": req.agency_inventory_id,
        "advertiser_id": req.advertiser_id,
        "campaign_id": req.campaign_id, "target_id": target_id,
        "target_name": req.target_name, "scale": scale,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "removed": False,
        "id_resolved": commission_id is not None,
    })
    msg = "Комиссия создана"
    if commission_id is None:
        msg += " (id не удалось определить — Update/Delete недоступны для неё)"
    else:
        msg += f" (id={resolved.get('overall_id')})"
    return CommissionResult(ok=True, dry_run=False, commission_id=commission_id,
                            crm_status=status, message=msg)


@router.post("/commission/update", response_model=CommissionResult)
async def update_commission(req: UpdateRequest) -> CommissionResult:
    if not settings.crm_enabled:
        raise HTTPException(status_code=403, detail="CRM-модуль выключен")

    rows = _load_registry()
    row = next((r for r in rows if r.get("commission_id") == req.commission_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="комиссия не найдена в реестре сервиса")

    scale = int(round(req.commission_scale))
    if scale <= 0 or scale > 100:
        raise HTTPException(status_code=400, detail="commission_scale должен быть в (0, 100]")

    td_id = _td_id(row["tenant"])
    # CommissionEntity для Update — как при создании, по тому же уровню
    ent_req = CommissionRequest(
        level=row["level"], tenant=row["tenant"], agency_id=row["agency_id"],
        agency_inventory_id=row.get("agency_inventory_id", ""),
        advertiser_id=row.get("advertiser_id"), campaign_id=row.get("campaign_id"),
        commission_scale=scale,
    )
    payload = {
        "CommissionEntity": _build_entity(ent_req, td_id),
        "Commission": {
            "Id": req.commission_id, "CommissionScale": scale,
            "DeviceType": None, "OS": None, "TrafficType": None,
            "BannerType": None, "Rewarded": None, "SspNumber": None,
        },
    }
    base = {"at": datetime.utcnow().isoformat(), "op": "update",
            "commission_id": req.commission_id, "commission_scale": scale}

    if settings.crm_dry_run:
        _append_log({**base, "mode": "dry_run", "payload": payload})
        return CommissionResult(ok=True, dry_run=True,
                                message=f"DRY-RUN: изменение на {scale}% НЕ отправлено.")

    status, text = await _crm_request("POST", "/core/AgencyCommission/Update", json=payload)
    ok = 200 <= status < 300
    _append_log({**base, "mode": "live", "payload": payload,
                 "crm_status": status, "crm_response": text[:500], "ok": ok})
    if not ok:
        return CommissionResult(ok=False, dry_run=False, commission_id=req.commission_id,
                                crm_status=status, message=f"CRM вернул HTTP {status}")

    # ВАЖНО: Update в CRM = replace — старая комиссия помечается removed,
    # создаётся НОВАЯ с новым id/overallId. Поэтому после Update заново
    # резолвим id, иначе реестр будет хранить мёртвый id и Delete промахнётся.
    new = await _resolve_commission(ent_req)
    new_id = new.get("id") if new else None
    row["scale"] = scale
    row["updated_at"] = datetime.utcnow().isoformat()
    if new_id:
        row["commission_id"] = new_id
        row["overall_id"] = new.get("overall_id")
        row["id_resolved"] = True
    else:
        # не нашли новый id — помечаем чтобы кнопки изменить/удалить пропали
        row["id_resolved"] = False
    _save_registry(rows)
    msg = "Комиссия изменена"
    if new_id:
        msg += f" (новый id={new.get('overall_id')})"
    else:
        msg += " — но новый id не определён, обнови вкладку «Комиссии»"
    return CommissionResult(ok=True, dry_run=False, commission_id=new_id,
                            crm_status=status, message=msg)


@router.delete("/commission/{commission_id}", response_model=CommissionResult)
async def delete_commission(commission_id: str) -> CommissionResult:
    if not settings.crm_enabled:
        raise HTTPException(status_code=403, detail="CRM-модуль выключен")

    rows = _load_registry()
    row = next((r for r in rows if r.get("commission_id") == commission_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="комиссия не найдена в реестре сервиса")

    base = {"at": datetime.utcnow().isoformat(), "op": "delete",
            "commission_id": commission_id}

    if settings.crm_dry_run:
        _append_log({**base, "mode": "dry_run"})
        return CommissionResult(ok=True, dry_run=True,
                                message="DRY-RUN: удаление НЕ отправлено.")

    status, text = await _crm_request(
        "DELETE", f"/core/AgencyCommission/Delete?id={commission_id}")
    ok = 200 <= status < 300
    _append_log({**base, "mode": "live", "crm_status": status,
                 "crm_response": text[:500], "ok": ok})
    if ok:
        _registry_mark_removed(commission_id)
    return CommissionResult(ok=ok, dry_run=False, commission_id=commission_id,
                            crm_status=status,
                            message="Комиссия удалена" if ok else f"CRM вернул HTTP {status}")


@router.get("/log")
async def commission_log() -> list:
    if not _LOG_FILE.exists():
        return []
    try:
        return json.loads(_LOG_FILE.read_text())
    except Exception:
        return []
