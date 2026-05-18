from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from .config import settings
from .models import (
    CampaignPaceOut,
    HybridCampaign,
    HybridPriceLimit,
    SignalLevel,
    currency_code,
    currency_symbol,
)


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
    advertiser_name: str,
    currency: int,
    c: HybridCampaign,
    period_start: date,
    period_end: date,
) -> CampaignPaceOut:
    today = period_end
    # если startDate отсутствует — берём начало окна (битый кейс)
    start = _to_date(c.startDate) if c.startDate else period_start
    end = _to_date(c.endDate) if c.endDate else None

    has_end_date = end is not None and not c.isDontExpire

    days_total = (end - start).days + 1 if end else None
    days_left = (end - today).days if end else None
    days_passed_from_start = max(1, (today - start).days + 1)
    # MTD-окно: для текущего отображения в столбце "Текущий месяц"
    pace_window_start = max(start, period_start)
    days_passed_window = max(1, (today - pace_window_start).days + 1)

    limit_kind, limit = _pick_active_limit(c)
    limit_unit = limit.unit if limit else "none"
    today_fact, period_fact = c.fact_for_unit(limit_unit) if limit else (0.0, 0.0)
    lifetime_fact = c.lifetime_fact(limit_unit) if limit else 0.0

    daily_target: Optional[float] = None
    period_budget: Optional[float] = None

    if limit_kind == "daily" and limit:
        daily_target = limit.amount
    elif limit_kind == "period_budget" and limit and days_total:
        period_budget = limit.amount
        daily_target = limit.amount / days_total
    elif limit_kind == "total" and limit:
        if days_total:
            daily_target = limit.amount / days_total
        else:
            daily_target = limit.amount / days_passed_from_start

    pace_today: Optional[float] = None
    pace_overall: Optional[float] = None
    if daily_target and daily_target > 0:
        pace_today = today_fact / daily_target
        if has_end_date:
            # есть дата окончания → pace_overall меряем по всему жизненному циклу
            # кампании: что накопилось от старта до сегодня против ожидаемого
            # за это же количество дней.
            expected_by_today = daily_target * min(days_passed_from_start, days_total or days_passed_from_start)
            if expected_by_today > 0:
                pace_overall = lifetime_fact / expected_by_today
        # без даты окончания pace_overall не определён (см. _decide_signal)

    signal, reason = _decide_signal(
        is_dont_expire=c.isDontExpire,
        has_end_date=has_end_date,
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
        advertiser_name=advertiser_name,
        currency=currency,
        currency_code=currency_code(currency),
        currency_symbol=currency_symbol(currency),
        campaign_id=c.id,
        campaign_name=c.name,
        status=c.status,
        start_date=c.startDate,
        end_date=c.endDate,
        is_dont_expire=c.isDontExpire,
        days_total=days_total,
        days_passed=days_passed_window,
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
    has_end_date: bool,
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
        return SignalLevel.NOT_STARTED, "ещё не стартовала"
    if end and end < today:
        return SignalLevel.FINISHED, "уже закончилась"
    if limit_kind == "none" or pace_today is None:
        return SignalLevel.NO_LIMIT, "лимит не настроен"

    pct_today = int(round((pace_today or 0) * 100))

    if not has_end_date:
        # Без даты окончания (или isDontExpire) — общего лимита быть не может,
        # ориентируемся только на дневной.
        if pace_today >= 1.0:
            return (
                SignalLevel.GREEN,
                f"идёт по плану по суточному: {pct_today}% (нет даты окончания — общий план не считаем)",
            )
        if pace_today >= 0.7:
            return (
                SignalLevel.YELLOW,
                f"отстаёт по суточному: {pct_today}% от дневного (норма ≥100%, нет даты окончания)",
            )
        return (
            SignalLevel.RED,
            f"сильно отстаёт по суточному: {pct_today}% от дневного (красный <70%, нет даты окончания)",
        )

    # Есть end_date — основная метрика pace_overall по всему периоду start..end.
    if pace_overall is None:
        return SignalLevel.NO_LIMIT, "лимит не настроен"

    pct = int(round(pace_overall * 100))
    tail = f", {days_left} дн. до конца" if days_left is not None else ""
    if pace_overall >= 1.0:
        return (
            SignalLevel.GREEN,
            f"идёт по плану: {pct}% от плана периода (start–end), сегодня {pct_today}% от дневного{tail}",
        )
    if pace_overall >= 0.7:
        return (
            SignalLevel.YELLOW,
            f"отстаёт: {pct}% от плана периода (норма ≥100%), сегодня {pct_today}% от дневного{tail}",
        )
    return (
        SignalLevel.RED,
        f"сильно отстаёт: {pct}% от плана периода (красный <70%), сегодня {pct_today}% от дневного{tail}",
    )
