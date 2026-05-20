from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


PRICE_FORMATION_UNIT = {
    1: "impressions",
    2: "clicks",
    3: "money",
}


CURRENCY_BY_CODE: dict[int, tuple[str, str]] = {
    643: ("RUB", "₽"),
    810: ("RUB", "₽"),
    840: ("USD", "$"),
    978: ("EUR", "€"),
    980: ("UAH", "₴"),
    933: ("BYN", "Br"),
    398: ("KZT", "₸"),
    156: ("CNY", "¥"),
    348: ("HUF", "Ft"),
    826: ("GBP", "£"),
}


def currency_code(code: int) -> str:
    return CURRENCY_BY_CODE.get(code, (f"#{code}", ""))[0]


def currency_symbol(code: int) -> str:
    return CURRENCY_BY_CODE.get(code, (f"#{code}", ""))[1]


class HybridPriceLimit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    priceFormationType: int
    amount: float

    @property
    def unit(self) -> str:
        return PRICE_FORMATION_UNIT.get(self.priceFormationType, f"unknown_{self.priceFormationType}")


class HybridCampaign(BaseModel):
    """Только нужные поля из ответа agencyStatistic/GetTotal.
    Делаем максимум полей необязательными — у Hybrid'а ответ для битых/
    новых/без-стат кампаний бывает усечён."""
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    status: int = 0
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    isDontExpire: bool = False

    dailyMultiPriceLimitations: list[HybridPriceLimit] = Field(default_factory=list)
    totalMultiPriceLimitations: list[HybridPriceLimit] = Field(default_factory=list)
    periodBudgetMultiPriceLimitations: list[HybridPriceLimit] = Field(default_factory=list)

    # факт за разные периоды
    todayImpressions: float = 0.0
    todaySum: float = 0.0
    todayClick: float = 0.0
    totalPeriodImpressions: float = 0.0
    totalPeriodSum: float = 0.0
    totalPeriodClick: float = 0.0
    impressionCount: int = 0
    totalSum: float = 0.0

    def fact_for_unit(self, unit: str) -> tuple[float, float]:
        """Возвращает (today_fact, window_fact) для указанной единицы лимита.
        - today_fact = self.today* (это РАСХОД ЗА СЕГОДНЯ по серверу Hybrid'а,
          константа от окна запроса — НЕ совпадает с "за переданный день").
        - window_fact = self.totalSum / self.impressionCount — это **факт за
          переданное startDate..endDate** окно (это меняется в зависимости от
          запроса; именно его мы используем как "вчера" при окне = [вчера, вчера]).

        ВНИМАНИЕ: поля totalPeriod* от Hybrid'а — это LIFETIME (см. lifetime_fact),
        не путать с window!
        """
        if unit == "impressions":
            return self.todayImpressions, float(self.impressionCount or 0)
        if unit in ("money", "rubles"):
            return self.todaySum, self.totalSum
        if unit == "clicks":
            return self.todayClick, self.totalPeriodClick  # clicks lifetime/window не разделено в API
        return 0.0, 0.0

    def lifetime_fact(self, unit: str) -> float:
        """Совокупный факт с начала кампании (lifetime) — это totalPeriod*.
        Используется для расчёта pace_overall у кампаний с end_date."""
        if unit == "impressions":
            return float(self.totalPeriodImpressions or 0)
        if unit in ("money", "rubles"):
            return self.totalPeriodSum
        if unit == "clicks":
            return self.totalPeriodClick
        return 0.0


class HybridGetTotalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    campaigns: list[HybridCampaign] = Field(default_factory=list)


class SignalLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    NO_LIMIT = "no_limit"
    NOT_STARTED = "not_started"
    FINISHED = "finished"


class CampaignPaceOut(BaseModel):
    """То что отдаём наружу — для UI/JSON ответа."""
    agency: str
    agency_id: str = ""        # 24-hex id агентства (нужен для CRM-комиссий)
    tenant: str = ""           # label кабинета (Hybrid / Selfclick)
    advertiser_id: str
    advertiser_name: str = ""
    currency: int = 643
    currency_code: str = "RUB"
    currency_symbol: str = "₽"
    campaign_id: str
    campaign_name: str
    status: int

    start_date: datetime
    end_date: Optional[datetime]
    is_dont_expire: bool
    days_total: Optional[int]
    days_passed: int
    days_left: Optional[int]

    limit_kind: str  # "daily" | "period_budget" | "total" | "none"
    limit_unit: str  # "impressions" | "money" | "clicks" | "none"
    daily_target: Optional[float]  # сколько в день должны крутить (в limit_unit)
    period_budget: Optional[float]
    # Факт за вчера (полный закрытый день — основная метрика для решения о техкосте).
    # period_fact оставлен как алиас, чтобы старый кэш (cache.json) грузился без падений.
    yesterday_fact: float = 0.0
    period_fact: float = 0.0

    today_spent: float = 0.0
    period_spent: float = 0.0
    total_spent: float = 0.0
    impressions_total: float = 0.0

    pace_yesterday: Optional[float] = None  # факт_вчера / дневной_таргет
    pace_overall: Optional[float] = None    # lifetime_факт / (дневной_таргет * days_passed)

    signal: SignalLevel
    signal_reason: str
