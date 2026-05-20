from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from .config import settings
from .models import HybridCampaign, HybridGetTotalResponse

log = logging.getLogger("dsp_pacing.hybrid")


class HybridAuthError(RuntimeError):
    pass


class Account(BaseModel):
    """Аккаунт из GetSelfAccounts — может быть Agency, Advertiser или TradingDesk."""
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str  # "Agency" | "Advertiser" | ...
    name: str = ""
    inventoryId: Optional[str] = None
    roles: list[str] = []


class Advertiser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    balance: float = 0.0
    currency: int = 643
    domain: Optional[str] = None


class HybridClient:
    """Stateful клиент к Hybrid DSP. После switch_to_agency() сессия запоминает
    активное агентство — все последующие запросы (advertisers, stats) идут
    в его контексте.
    """

    DEFAULT_STAT_FIELDS = [2, 4, 58, 60, 59, 43, 1, 76, 77, 62, 61, 7, 6]
    USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        host: str,
        cookies_header: str,
        user_id: str,
        timezone_id: int,
        timeout: float = 30.0,
    ):
        self.host = host
        self.user_id = user_id
        self.timezone_id = timezone_id

        # парсим Cookie-header в dict для httpx.cookies jar
        cookies: dict[str, str] = {}
        for pair in cookies_header.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                cookies[k.strip()] = v.strip()

        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,  # ChangeAccount → 302, обрабатываем сами
            cookies=cookies,
            headers={
                "Referer": f"https://{host}/",
                "User-Agent": self.USER_AGENT,
            },
        )
        self._active_account_id: Optional[str] = None

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- HTTP с ретраями ----------

    MAX_RETRIES = 4
    RETRY_DELAYS = [0.5, 1.5, 3.0, 6.0]  # пауза перед попыткой #2,3,4,...

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """HTTP-запрос с ретраями на сетевые ошибки (ConnectTimeout/ReadTimeout/etc).
        Один сетевой чих не должен валить весь sync."""
        last_err: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._client.request(method, url, **kwargs)
            except httpx.TransportError as e:
                last_err = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    log.warning(
                        "%s %s — %s (retry %d/%d in %.1fs)",
                        method, url.rsplit("/", 1)[-1], type(e).__name__,
                        attempt + 1, self.MAX_RETRIES - 1, delay,
                    )
                    await asyncio.sleep(delay)
        raise last_err  # type: ignore[misc]

    # ---------- accounts (agencies / advertisers) ----------

    async def list_self_accounts(self) -> list[Account]:
        url = f"https://{self.host}/core/account/GetSelfAccounts"
        r = await self._request("GET", url, headers={"Accept": "application/json, text/plain, */*"})
        self._check_auth(r)
        r.raise_for_status()
        return [Account.model_validate(x) for x in r.json()]

    async def list_agencies(self) -> list[Account]:
        accs = await self.list_self_accounts()
        return [a for a in accs if a.type == "Agency"]

    async def switch_to_agency(self, agency_id: str) -> None:
        """Переключает активное агентство в сессии (обновляет csid в jar)."""
        if self._active_account_id == agency_id:
            return
        url = f"https://{self.host}/core/login/ChangeAccount"
        r = await self._request(
            "GET",
            url,
            params={"userId": self.user_id, "accountId": agency_id},
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        # ChangeAccount возвращает 302 на /, само переходить не нужно — Set-Cookie уже применён
        if r.status_code not in (200, 302):
            raise HybridAuthError(
                f"ChangeAccount({agency_id}) → {r.status_code}: {r.text[:200]}"
            )
        self._active_account_id = agency_id

    async def list_advertisers(self) -> list[Advertiser]:
        """Рекламодатели текущего активного агентства."""
        url = f"https://{self.host}/core/advertisers/GetAll"
        r = await self._request(
            "GET",
            url,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self._check_auth(r)
        r.raise_for_status()
        return [Advertiser.model_validate(x) for x in r.json()]

    # ---------- статистика ----------

    async def get_total(
        self,
        advertiser_id: str,
        start: date,
        end: date,
    ) -> HybridGetTotalResponse:
        url = f"https://{self.host}/core/agencyStatistic/GetTotal"
        params = {
            "advertiserId": advertiser_id,
            "startDate": datetime.combine(start, time.min).isoformat(),
            "endDate": datetime.combine(end, time(23, 59, 59)).isoformat(),
            "campaignFilter": "0",
            "searchQuery": "",
            "searchType": "0",
            "timeZoneId": str(self.timezone_id),
        }
        body = {
            "fields": self.DEFAULT_STAT_FIELDS,
            "dynamicFields": [],
            "conversionFields": [],
            "conversionSortField": [],
            "metricIds": [],
        }
        r = await self._request(
            "POST",
            url,
            params=params,
            json=body,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/json",
            },
        )
        self._check_auth(r)
        r.raise_for_status()
        # парсим терпимо: битые кампании пропускаем индивидуально
        raw = r.json() or {}
        raw_campaigns = raw.get("campaigns") or []
        ok: list[HybridCampaign] = []
        for c in raw_campaigns:
            try:
                ok.append(HybridCampaign.model_validate(c))
            except ValidationError as e:
                log.warning(
                    "      bad campaign id=%s: %d errors", c.get("id"), e.error_count()
                )
        return HybridGetTotalResponse(campaigns=ok)

    def _check_auth(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise HybridAuthError(f"cookies протухли (status={r.status_code})")


def make_client() -> HybridClient:
    return HybridClient(
        host=settings.hybrid_host,
        cookies_header=settings.hybrid_cookies,
        user_id=settings.hybrid_user_id,
        timezone_id=settings.hybrid_timezone_id,
    )


def make_client_for(tenant) -> "HybridClient":
    """Создать клиент под конкретный tenant (см. config.TenantConfig)."""
    return HybridClient(
        host=tenant.host,
        cookies_header=tenant.cookies,
        user_id=tenant.user_id,
        timezone_id=settings.hybrid_timezone_id,
    )
