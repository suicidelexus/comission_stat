from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


PRICE_FORMATION_UNIT = {
    1: "impressions",
    2: "clicks",
    3: "rubles",
}


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
        """Возвращает (today_fact, period_fact) для указанной единицы лимита."""
        if unit == "impressions":
            return self.todayImpressions, self.totalPeriodImpressions
        if unit == "rubles":
            return self.todaySum, self.totalPeriodSum
        if unit == "clicks":
            return self.todayClick, self.totalPeriodClick
        return 0.0, 0.0


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
    advertiser_id: str
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
    limit_unit: str  # "impressions" | "rubles" | "clicks" | "none"
    daily_target: Optional[float]  # сколько в день должны крутить (в limit_unit)
    period_budget: Optional[float]
    today_fact: float  # факт сегодня в limit_unit
    period_fact: float  # факт за окно в limit_unit

    today_spent: float
    period_spent: float
    total_spent: float
    impressions_total: float

    pace_today: Optional[float]   # факт_сегодня / дневной_таргет
    pace_overall: Optional[float] # факт_за_период / (дневной_таргет * дни_прошли)

    signal: SignalLevel
    signal_reason: str
