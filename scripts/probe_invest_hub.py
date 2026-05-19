"""Гляну сырой ответ Hybrid'а на Programmatic Бизнес Солюшнс — какие лимиты
у 'Инвест хаб выгода': только daily, или есть и periodBudget."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings
from app.hybrid_client import make_client_for


async def main():
    # Programmatic.ru // Бизнес Солюшнс = 67e12b8e4d506e22b8adcb68 в hybrid.ai
    AGENCY = "67e12b8e4d506e22b8adcb68"
    tenant = next(t for t in settings.tenants if t.label == "Hybrid")
    client = make_client_for(tenant)

    await client.switch_to_agency(AGENCY)
    advs = await client.list_advertisers()

    yesterday = date.today() - timedelta(days=1)
    for adv in advs:
        resp = await client.get_total(adv.id, yesterday, yesterday)
        for c in resp.campaigns:
            if "Инвест хаб" in c.name or "ИНВЕСТ ХАБ" in c.name:
                print(json.dumps({
                    "id": c.id,
                    "name": c.name,
                    "startDate": str(c.startDate),
                    "endDate": str(c.endDate),
                    "isDontExpire": c.isDontExpire,
                    "status": c.status,
                    "dailyMultiPriceLimitations": [x.model_dump() for x in c.dailyMultiPriceLimitations],
                    "totalMultiPriceLimitations": [x.model_dump() for x in c.totalMultiPriceLimitations],
                    "periodBudgetMultiPriceLimitations": [x.model_dump() for x in c.periodBudgetMultiPriceLimitations],
                    "todaySum": c.todaySum,
                    "todayImpressions": c.todayImpressions,
                    "totalSum (window=yesterday)": c.totalSum,
                    "impressionCount (window=yesterday)": c.impressionCount,
                    "totalPeriodSum (lifetime)": c.totalPeriodSum,
                    "totalPeriodImpressions (lifetime)": c.totalPeriodImpressions,
                }, ensure_ascii=False, indent=2))
                break

    await client.close()


asyncio.run(main())
