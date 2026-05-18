"""Перебор эндпоинтов с реальным tradingDeskId."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_auth import load_env  # type: ignore

TD_ID = "6249cd14b232323fbce8f5f6"  # console.artics.ru

# с конкретным id пробуем разные эндпоинты + разные имена параметра
CANDIDATES = []
for path in [
    "/core/tradingDesk/Get",
    "/core/tradingdesk/Get",
    "/core/tradingDesk/GetById",
    "/core/agency/GetByTradingDesk",
    "/core/agency/getpage",
    "/core/agency/getAgenciesByTradingDesk",
    "/core/agency/getAgencies",
    "/core/agencyList/Get",
    "/core/advertiser/GetByAgency",
    "/core/advertiser/getAdvertisersByAgency",
    "/core/advertiser/getpage",
    "/core/agencyManager/Get",
    "/core/tradingdeskManager/Get",
]:
    for pname in ["id", "tradingDeskId", "tradingdeskId", "ownerId", "agencyId"]:
        CANDIDATES.append(("GET", path, {pname: TD_ID}))

# плюс попробуем agencyStatistic/GetTotal с tradingDeskId вместо advertiserId
from datetime import date
today = date.today().isoformat()
start = today + "T00:00:00"
end = today + "T23:59:59"
CANDIDATES.append(
    ("POST", "/core/agencyStatistic/GetTotal", {
        "tradingDeskId": TD_ID,
        "startDate": start, "endDate": end,
        "campaignFilter": "0", "searchQuery": "", "searchType": "0", "timeZoneId": "305",
    })
)
CANDIDATES.append(
    ("POST", "/core/agencyStatistic/GetTotal", {
        "agencyId": TD_ID,
        "startDate": start, "endDate": end,
        "campaignFilter": "0", "searchQuery": "", "searchType": "0", "timeZoneId": "305",
    })
)


def main() -> int:
    env = load_env()
    host = env["HYBRID_HOST"]
    cookies = env["HYBRID_COOKIES"]
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
        "Referer": f"https://{host}/",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookies,
    }

    hits = []
    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        for method, path, params in CANDIDATES:
            url = f"https://{host}{path}"
            try:
                if method == "POST":
                    r = client.post(url, params=params, headers=headers, json={
                        "fields": [2,4,58,60,59,43,1,76,77,62,61,7,6],
                        "dynamicFields": [], "conversionFields": [],
                        "conversionSortField": [], "metricIds": [],
                    })
                else:
                    r = client.get(url, params=params, headers=headers)
            except Exception as e:
                print(f"  ERR {method} {path}: {e}")
                continue
            ctype = r.headers.get("content-type", "")
            ok = r.status_code == 200 and "json" in ctype
            tag = "✅" if ok else ("⚠️" if r.status_code != 404 else "❌")
            # из 404 мы получим тонну шума — покажем только не-404
            if r.status_code == 404:
                continue
            param_str = ",".join(params.keys()) if params else ""
            preview = r.text[:200].replace("\n", " ") if r.content else ""
            print(f"  {tag} {r.status_code:>3} {method} {path}  ({param_str})  {len(r.content)}b  {preview}")
            if ok:
                hits.append((method, path, params))

    print(f"\n=== HITS: {len(hits)} ===")
    for m, p, q in hits:
        print(f"  {m} {p}  {q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
