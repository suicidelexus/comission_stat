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
    """period_start..period_end — окно которое мы запросили у Hybrid'а.
    В новой логике это РОВНО вчера (1 день), поэтому period_fact == yesterday_fact.
    """
    today = date.today()
    # если startDate отсутствует — берём начало окна (битый кейс)
    start = _to_date(c.startDate) if c.startDate else period_start
    end = _to_date(c.endDate) if c.endDate else None

    has_end_date = end is not None and not c.isDontExpire

    days_total = (end - start).days + 1 if end else None
    days_left = (end - today).days if end else None
    days_passed_from_start = max(1, (today - start).days + 1)
    # дни в окне запроса (для информации; сейчас всегда 1, потому что запрос за вчера)
    pace_window_start = max(start, period_start)
    days_passed_window = max(1, (period_end - pace_window_start).days + 1)

    limit_kind, limit = _pick_active_limit(c)
    limit_unit = limit.unit if limit else "none"
    # окно у нас = вчера (1 день), значит period_fact из Hybrid'а — это и есть факт вчера
    _today_fact_unused, yesterday_fact = c.fact_for_unit(limit_unit) if limit else (0.0, 0.0)
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

    pace_yesterday: Optional[float] = None
    pace_overall: Optional[float] = None
    if daily_target and daily_target > 0:
        pace_yesterday = yesterday_fact / daily_target
        if has_end_date and days_total:
            # ПРОГНОЗ выполнения плана за весь период start..end:
            # projected_total = (что уже открутили) + (что докрутят оставшимися днями
            #                   с темпом = вчера). Сравниваем с планом за весь период.
            # > 100% — перевыполнит план, < 100% — недокрутит.
            planned_total = daily_target * days_total
            days_remaining = max(0, (end - today).days)  # сегодня уже сегодня
            projected_remaining = (yesterday_fact or 0) * days_remaining
            projected_total = lifetime_fact + projected_remaining
            if planned_total > 0:
                pace_overall = projected_total / planned_total
        # без даты окончания pace_overall не определён (см. _decide_signal)

    signal, reason = _decide_signal(
        is_dont_expire=c.isDontExpire,
        has_end_date=has_end_date,
        start=start,
        end=end,
        today=today,
        days_left=days_left,
        limit_kind=limit_kind,
        pace_yesterday=pace_yesterday,
        pace_overall=pace_overall,
        yesterday_fact=yesterday_fact,
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
        yesterday_fact=yesterday_fact,
        period_fact=yesterday_fact,  # алиас для совместимости со старыми клиентами
        today_spent=c.todaySum,
        period_spent=c.totalPeriodSum,
        total_spent=c.totalSum,
        impressions_total=c.totalPeriodImpressions,
        pace_yesterday=pace_yesterday,
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
    pace_yesterday: Optional[float],
    pace_overall: Optional[float],
    yesterday_fact: float,
    status: int,
) -> tuple[SignalLevel, str]:
    if start > today:
        return SignalLevel.NOT_STARTED, "ещё не стартовала"
    if end and end < today:
        return SignalLevel.FINISHED, "уже закончилась"
    if limit_kind == "none" or pace_yesterday is None:
        return SignalLevel.NO_LIMIT, "лимит не настроен"

    pct_yesterday = int(round((pace_yesterday or 0) * 100))

    if not has_end_date:
        # Без даты окончания (или isDontExpire) — общего лимита быть не может,
        # ориентируемся только на дневной (по факту за вчера).
        if pace_yesterday >= 1.0:
            return (
                SignalLevel.GREEN,
                f"вчера выполнила суточный: {pct_yesterday}% (нет даты окончания — общий план не считаем)",
            )
        if pace_yesterday >= 0.7:
            return (
                SignalLevel.YELLOW,
                f"вчера отстала по суточному: {pct_yesterday}% (норма ≥100%, нет даты окончания)",
            )
        return (
            SignalLevel.RED,
            f"вчера сильно недокрутила: {pct_yesterday}% от дневного (красный <70%, нет даты окончания)",
        )

    # Есть end_date — основная метрика: прогноз выполнения плана за период.
    if pace_overall is None:
        return SignalLevel.NO_LIMIT, "лимит не настроен"

    pct = int(round(pace_overall * 100))
    tail = f", осталось {days_left} дн." if days_left is not None else ""
    if pace_overall >= 1.0:
        return (
            SignalLevel.GREEN,
            f"прогноз: {pct}% от плана периода (выполнит/перевыполнит при текущем темпе), "
            f"вчера {pct_yesterday}% от дневного{tail}",
        )
    if pace_overall >= 0.7:
        return (
            SignalLevel.YELLOW,
            f"прогноз: {pct}% от плана периода (немного не дотянет, норма ≥100%), "
            f"вчера {pct_yesterday}% от дневного{tail}",
        )
    return (
        SignalLevel.RED,
        f"прогноз: {pct}% от плана периода (сильно не дотянет, красный <70%), "
        f"вчера {pct_yesterday}% от дневного{tail}",
    )
