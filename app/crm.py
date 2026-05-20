"""CRM-интеграция: создание кастомных комиссий (техкост) на кампании.

═══════════════════════════════════════════════════════════════════════
ИЗОЛИРОВАННЫЙ МОДУЛЬ. Чтобы полностью вырезать CRM из проекта:
  1. удалить этот файл (app/crm.py)
  2. убрать в app/main.py 2 строки с "crm" (импорт + include_router)
  3. убрать в app/static/index.html блок между <!-- CRM:start --> и <!-- CRM:end -->
Больше нигде CRM не используется.
═══════════════════════════════════════════════════════════════════════

ДВА ПРЕДОХРАНИТЕЛЯ (app/config.py):
  CRM_ENABLED=false  — модуль выключен, все endpoint'ы отдают 403.
  CRM_DRY_RUN=true   — включённый модуль только логирует, НЕ шлёт в CRM.

Каждая операция (и dry-run, и боевая) пишется в commissions_log.json.
"""
from __future__ import annotations

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


# ---------- модели запроса/ответа ----------

class CommissionRequest(BaseModel):
    """То что присылает UI при нажатии «Создать комиссию»."""
    campaign_id: str
    advertiser_id: str
    agency_id: str
    tenant: str               # label кабинета — по нему берём TradingDeskId
    commission_scale: float   # размер техкоста (%)
    campaign_name: str = ""   # для лога/читаемости


class CommissionResult(BaseModel):
    ok: bool
    dry_run: bool
    message: str
    crm_status: Optional[int] = None
    crm_response: Optional[str] = None


# ---------- лог операций ----------

def _append_log(entry: dict) -> None:
    try:
        data: list = []
        if _LOG_FILE.exists():
            data = json.loads(_LOG_FILE.read_text())
        data.append(entry)
        _LOG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning("commission log write failed: %s", e)


# ---------- CRM HTTP-клиент ----------

class CRMError(RuntimeError):
    pass


async def _post_commission(payload: dict) -> tuple[int, str]:
    """Реальный POST в CRM. Вызывается только в боевом режиме."""
    cookies: dict[str, str] = {}
    for pair in settings.crm_cookies.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookies[k.strip()] = v.strip()

    url = f"https://{settings.crm_host}/core/AgencyCommission/Create"
    async with httpx.AsyncClient(timeout=30.0, cookies=cookies) as client:
        r = await client.post(
            url,
            json=payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": f"https://{settings.crm_host}",
                "Referer": f"https://{settings.crm_host}/panel/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
            },
        )
    return r.status_code, r.text[:1000]


def _build_payload(req: CommissionRequest, trading_desk_id: str) -> dict:
    """Собирает тело запроса в формате CRM AgencyCommission/Create."""
    return {
        "CommissionEntity": {
            "AgencyId": req.agency_id,
            "AdvertiserId": req.advertiser_id,
            "FolderId": None,
            "CampaignId": req.campaign_id,
            "TradingDeskId": trading_desk_id,
        },
        "Commission": {
            "Id": None,
            "CommissionScale": req.commission_scale,
            "DeviceType": None,
            "OS": None,
            "TrafficType": None,
            "BannerType": None,
            "Rewarded": None,
            "SspNumber": None,
        },
    }


# ---------- endpoint'ы ----------

@router.get("/status")
async def crm_status() -> dict:
    """Состояние CRM-модуля — UI читает чтобы решить показывать ли кнопки."""
    return {
        "enabled": settings.crm_enabled,
        "dry_run": settings.crm_dry_run,
        "host": settings.crm_host,
        "trading_desks": settings.crm_td_map,
        "cookies_set": bool(settings.crm_cookies.strip()),
    }


@router.post("/commission", response_model=CommissionResult)
async def create_commission(req: CommissionRequest) -> CommissionResult:
    """Создать кастомную комиссию (техкост) на кампанию.
    Защита: CRM_ENABLED обязателен; в CRM_DRY_RUN только логирует."""
    if not settings.crm_enabled:
        raise HTTPException(status_code=403, detail="CRM-модуль выключен (CRM_ENABLED=false)")

    # резолвим TradingDeskId по кабинету
    td_id = settings.crm_td_map.get(req.tenant)
    if not td_id:
        raise HTTPException(
            status_code=400,
            detail=f"нет TradingDeskId для кабинета '{req.tenant}' "
                   f"— проверь CRM_TRADING_DESK_IDS в .env",
        )

    if req.commission_scale <= 0 or req.commission_scale > 100:
        raise HTTPException(status_code=400, detail="commission_scale должен быть в (0, 100]")

    payload = _build_payload(req, td_id)
    base_entry = {
        "at": datetime.utcnow().isoformat(),
        "campaign_id": req.campaign_id,
        "campaign_name": req.campaign_name,
        "advertiser_id": req.advertiser_id,
        "agency_id": req.agency_id,
        "tenant": req.tenant,
        "trading_desk_id": td_id,
        "commission_scale": req.commission_scale,
    }

    # DRY-RUN: только логируем, ничего не шлём
    if settings.crm_dry_run:
        entry = {**base_entry, "mode": "dry_run", "payload": payload}
        _append_log(entry)
        log.info("[CRM dry-run] commission %.2f%% on %s — НЕ отправлено",
                 req.commission_scale, req.campaign_name or req.campaign_id)
        return CommissionResult(
            ok=True, dry_run=True,
            message=f"DRY-RUN: комиссия {req.commission_scale}% НЕ отправлена "
                    f"(сними CRM_DRY_RUN чтобы создавать реально). Залогировано.",
        )

    # БОЕВОЙ режим
    try:
        status, body = await _post_commission(payload)
    except Exception as e:
        _append_log({**base_entry, "mode": "live", "error": str(e)})
        log.exception("CRM commission failed")
        raise HTTPException(status_code=502, detail=f"CRM запрос упал: {e}")

    ok = 200 <= status < 300
    _append_log({**base_entry, "mode": "live", "crm_status": status,
                 "crm_response": body, "ok": ok})
    log.info("[CRM live] commission %.2f%% on %s → HTTP %d",
             req.commission_scale, req.campaign_name or req.campaign_id, status)

    return CommissionResult(
        ok=ok, dry_run=False,
        message="Комиссия создана" if ok else f"CRM вернул HTTP {status}",
        crm_status=status, crm_response=body,
    )


@router.get("/log")
async def commission_log() -> list:
    """История созданных комиссий (и dry-run, и боевых)."""
    if not _LOG_FILE.exists():
        return []
    try:
        return json.loads(_LOG_FILE.read_text())
    except Exception:
        return []
