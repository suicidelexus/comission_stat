"""Распотрошить ответ GetTotal: уникальные ключи, наличие endDate, типы лимитов."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_auth import load_env  # type: ignore


def main() -> int:
    env = load_env()
    host = env["HYBRID_HOST"]
    cookies = env["HYBRID_COOKIES"]

    url = f"https://{host}/core/agencyStatistic/GetTotal"
    params = {
        "advertiserId": "69650250810d989cb85daf20",
        "startDate": "2026-05-17T00:00:00",
        "endDate": "2026-05-17T23:59:59",
        "campaignFilter": "0",
        "searchQuery": "",
        "searchType": "0",
        "timeZoneId": "305",
    }
    body = {
        "fields": [2, 4, 58, 60, 59, 43, 1, 76, 77, 62, 61, 7, 6],
        "dynamicFields": [],
        "conversionFields": [],
        "conversionSortField": [],
        "metricIds": [],
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
        "Referer": f"https://{host}/",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookies,
    }

    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, params=params, headers=headers, json=body)
    r.raise_for_status()
    data = r.json()

    # сохраним сырой ответ
    out_path = Path(__file__).resolve().parent.parent / "tmp_total_response.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"saved: {out_path}")

    print("\n=== top-level keys ===")
    if isinstance(data, dict):
        for k, v in data.items():
            tag = type(v).__name__
            if isinstance(v, list):
                tag = f"list[{len(v)}]"
            print(f"  {k}: {tag}")

    campaigns = data.get("campaigns") or []
    print(f"\n=== campaigns: {len(campaigns)} total ===")

    if not campaigns:
        return 0

    # уникальные ключи по всем кампаниям
    all_keys: set[str] = set()
    has_end_date: list[dict] = []
    has_total_limit: list[dict] = []
    has_daily_limit: list[dict] = []
    has_period_budget: list[dict] = []
    no_limits: list[dict] = []

    for c in campaigns:
        all_keys |= set(c.keys())
        end = c.get("endDate")
        if end:
            has_end_date.append(c)
        if c.get("totalMultiPriceLimitations"):
            has_total_limit.append(c)
        if c.get("dailyMultiPriceLimitations"):
            has_daily_limit.append(c)
        if c.get("periodBudgetMultiPriceLimitations"):
            has_period_budget.append(c)
        if not (
            c.get("totalMultiPriceLimitations")
            or c.get("dailyMultiPriceLimitations")
            or c.get("periodBudgetMultiPriceLimitations")
        ):
            no_limits.append(c)

    print(f"unique keys: {len(all_keys)}")
    for k in sorted(all_keys):
        print(f"  {k}")

    print("\n=== summary ===")
    print(f"with endDate:                          {len(has_end_date)}")
    print(f"with dailyMultiPriceLimitations:       {len(has_daily_limit)}")
    print(f"with totalMultiPriceLimitations:       {len(has_total_limit)}")
    print(f"with periodBudgetMultiPriceLimitations:{len(has_period_budget)}")
    print(f"with NO limits at all:                 {len(no_limits)}")

    def show(label: str, items: list[dict], fields: list[str]) -> None:
        if not items:
            return
        print(f"\n--- {label} (показываю 2) ---")
        for c in items[:2]:
            slim = {k: c.get(k) for k in fields if k in c}
            print(json.dumps(slim, ensure_ascii=False, indent=2))

    common_fields = [
        "id", "name", "status", "startDate", "endDate", "isDontExpire",
        "dailyMultiPriceLimitations", "totalMultiPriceLimitations",
        "periodBudgetMultiPriceLimitations",
        "impressionCount", "totalSum", "totalPeriodSum", "todaySum",
    ]
    show("кампания с endDate", has_end_date, common_fields)
    show("кампания с totalMultiPriceLimitations", has_total_limit, common_fields)
    show("кампания с periodBudgetMultiPriceLimitations", has_period_budget, common_fields)

    return 0


if __name__ == "__main__":
    sys.exit(main())
