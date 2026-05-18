"""Прозвон agencyStatistic/GetTotal — итоги по рекламодателю за период."""
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
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        "Cookie": cookies,
    }

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        r = client.post(url, params=params, headers=headers, json=body)

    print(f"status: {r.status_code}")
    print(f"content-type: {r.headers.get('content-type')}")
    print(f"length: {len(r.content)} bytes")

    if r.status_code != 200:
        print(r.text[:1000])
        return 1

    try:
        data = r.json()
    except Exception as e:
        print(f"[!] not JSON: {e}")
        print(r.text[:1000])
        return 1

    print("\n--- response ---")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
