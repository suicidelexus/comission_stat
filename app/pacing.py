from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from .config import settings
from .models import CampaignPaceOut, HybridCampaign, HybridPriceLimit, SignalLevel


def _to_date(dt: datetime) -> date:
    return dt.date()


def _sum_amount(limits: list[HybridPriceLimit]) -> Optional[float]:
    if not limits:
        return None
    return sum(x.amount for x in limits)


def _pick_active_limit(c: HybridCampaign) -> tuple[str, Optional[HybridPriceLimit]]:
    """Выбирает какой лимит активен. Если несколько — берём первый.
    Возвращает (kind, limit). kind in {daily, period_budget, total, none}.
    """
    if c.dailyMultiPriceLimitations:
        return "daily", c.dailyMultiPriceLimitations[0]
    if c.periodBudgetMultiPriceLimitations:
        return "period_budget", c.periodBudgetMultiPriceLimitations[0]
    if c.totalMultiPriceLimitations:
        return "total", c.totalMultiPriceLimitations[0]
    return "none", None


def compute_pace(
    *,
    agency: str,
    advertiser_id: str,
    c: HybridCampaign,
    period_start: date,
    period_end: date,
) -> CampaignPaceOut:
    today = period_end
    # если startDate отсутствует — берём начало окна (битый кейс)
    start = _to_date(c.startDate) if c.startDate else period_start
    end = _to_date(c.endDate) if c.endDate else None

    days_total = (end - start).days + 1 if end else None
    days_left = (end - today).days if end else None
    pace_window_start = max(start, period_start)
    days_passed = max(1, (today - pace_window_start).days + 1)

    limit_kind, limit = _pick_active_limit(c)
    limit_unit = limit.unit if limit else "none"
    today_fact, period_fact = c.fact_for_unit(limit_unit) if limit else (0.0, 0.0)

    daily_target: Optional[float] = None
    period_budget: Optional[float] = None

    if limit_kind == "daily" and limit:
        daily_target = limit.amount
    elif limit_kind == "period_budget" and limit and days_total:
        period_budget = limit.amount
        daily_target = limit.amount / days_total
    elif limit_kind == "total" and limit:
        # total без даты — делим на прошедшие дни от старта кампании
        days_since_start = max(1, (today - start).days + 1)
        daily_target = limit.amount / days_since_start

    pace_today: Optional[float] = None
    pace_overall: Optional[float] = None
    if daily_target and daily_target > 0:
        pace_today = today_fact / daily_target
        pace_overall = period_fact / (daily_target * days_passed)

    signal, reason = _decide_signal(
        is_dont_expire=c.isDontExpire,
        start=start,
        end=end,
        today=today,
        days_left=days_left,
        limit_kind=limit_kind,
        pace_today=pace_today,
        pace_overall=pace_overall,
        today_fact=today_fact,
        status=c.status,
    )

    return CampaignPaceOut(
        agency=agency,
        advertiser_id=advertiser_id,
        campaign_id=c.id,
        campaign_name=c.name,
        status=c.status,
        start_date=c.startDate,
        end_date=c.endDate,
        is_dont_expire=c.isDontExpire,
        days_total=days_total,
        days_passed=days_passed,
        days_left=days_left,
        limit_kind=limit_kind,
        limit_unit=limit_unit,
        daily_target=daily_target,
        period_budget=period_budget,
        today_fact=today_fact,
        period_fact=period_fact,
        today_spent=c.todaySum,
        period_spent=c.totalPeriodSum,
        total_spent=c.totalSum,
        impressions_total=c.totalPeriodImpressions,
        pace_today=pace_today,
        pace_overall=pace_overall,
        signal=signal,
        signal_reason=reason,
    )


def _decide_signal(
    *,
    is_dont_expire: bool,
    start: date,
    end: Optional[date],
    today: date,
    days_left: Optional[int],
    limit_kind: str,
    pace_today: Optional[float],
    pace_overall: Optional[float],
    today_fact: float,
    status: int,
) -> tuple[SignalLevel, str]:
    if start > today:
        return SignalLevel.NOT_STARTED, "кампания ещё не стартовала"
    if end and end < today:
        return SignalLevel.FINISHED, "кампания уже закончилась"
    if limit_kind == "none" or pace_overall is None:
        return SignalLevel.NO_LIMIT, "лимиты не установлены"

    threshold = settings.pace_green_threshold
    min_left = settings.min_days_left_for_green

    enough_runway = is_dont_expire or days_left is None or days_left >= min_left
    actively_running = today_fact > 0
    main_pace = pace_overall

    if main_pace >= threshold:
        if not actively_running:
            return (
                SignalLevel.YELLOW,
                f"pace_overall={main_pace:.2f} ок, но сегодня кампания не крутит (today_fact=0)",
            )
        if enough_runway:
            return (
                SignalLevel.GREEN,
                f"pace_overall={main_pace:.2f}, pace_today={pace_today:.2f}, "
                f"days_left={days_left} — можно поднимать техкост",
            )
        return (
            SignalLevel.YELLOW,
            f"pace ок ({main_pace:.2f}), но days_left={days_left} < {min_left}",
        )

    if main_pace >= threshold * 0.8:
        return (
            SignalLevel.YELLOW,
            f"pace_overall={main_pace:.2f} — близко к {threshold:.2f}, но не дотягивает",
        )

    return (
        SignalLevel.RED,
        f"pace_overall={main_pace:.2f} — лимит не выполняется (порог {threshold:.2f})",
    )
