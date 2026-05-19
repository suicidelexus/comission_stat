"""Проверка: меняется ли todaySum/totalPeriodSum при разных окнах запроса."""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.hybrid_client import make_client


async def main():
    client = make_client()
    AGENCY = "623b0fb34d506e5070f96ac6"  # Iqueem Digital Marketing Agency
    ADV = "6593ecd17bc72f3f7058876c"     # Lujo Hotel
    CAMPAIGN = "67a5e4147bc72f0d6cedf790" # RUS - CPM - Sunset Villa - Travel Intent

    await client.switch_to_agency(AGENCY)

    for d in [date(2026, 5, 17), date(2026, 5, 18), date(2026, 5, 19)]:
        resp = await client.get_total(ADV, d, d)
        for c in resp.campaigns:
            if c.id == CAMPAIGN:
                print(f"=== окно {d} ... {d} ===")
                print(f"  todaySum:            {c.todaySum}")
                print(f"  todayImpressions:    {c.todayImpressions}")
                print(f"  totalPeriodSum:      {c.totalPeriodSum}")
                print(f"  totalPeriodImpr:     {c.totalPeriodImpressions}")
                print(f"  totalSum (lifetime?):{c.totalSum}")
                print(f"  impressionCount:     {c.impressionCount}")
                break

    await client.close()


asyncio.run(main())
