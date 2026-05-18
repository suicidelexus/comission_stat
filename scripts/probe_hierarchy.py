"""Тыркаем вероятные эндпоинты иерархии (TradingDesk/Agency/Advertiser) — что отзовётся."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_auth import load_env  # type: ignore

# наиболее вероятные паттерны
CANDIDATES = [
    # agencyStatistic — тут точно есть GetTotal, попробуем братьев
    ("GET", "/core/agencyStatistic/GetAdvertisers", None),
    ("GET", "/core/agencyStatistic/GetAgencies", None),
    ("GET", "/core/agencyStatistic/GetTradingDesks", None),
    ("GET", "/core/agencyStatistic/GetList", None),
    ("GET", "/core/agencyStatistic/GetData", None),
    # advertiser-related
    ("GET", "/core/advertiser/GetByAgency", None),
    ("GET", "/core/advertiser/getpage", None),
    ("GET", "/core/advertiserlist/Get", None),
    ("GET", "/core/advertiserList/Get", None),
    # agency
    ("GET", "/core/agency/getpage", None),
    ("GET", "/core/agencyList/Get", None),
    ("GET", "/core/agency/GetByTradingDesk", None),
    # trading desk
    ("GET", "/core/tradingDesk/getpage", None),
    ("GET", "/core/tradingDeskList/Get", None),
    # navigation/menu (классические места где сидит дерево скоупа)
    ("GET", "/core/menu/Get", None),
    ("GET", "/core/sidebar/Get", None),
    ("GET", "/core/navigation/Get", None),
    ("GET", "/core/scope/Get", None),
    # session / context
    ("GET", "/core/session/Get", None),
    ("GET", "/core/profile/Get", None),
    ("GET", "/core/currentUser/Get", None),
]


def main() -> int:
    env = load_env()
    host = env["HYBRID_HOST"]
    cookies = env["HYBRID_COOKIES"]
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{host}/",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookies,
    }

    with httpx.Client(timeout=15.0, follow_redirects=False) as client:
        for method, path, _ in CANDIDATES:
            url = f"https://{host}{path}"
            try:
                r = client.request(method, url, headers=headers)
            except Exception as e:
                print(f"  ERR {method} {path}: {e}")
                continue
            ctype = r.headers.get("content-type", "")
            ok = r.status_code == 200 and "json" in ctype
            tag = "✅" if ok else "❌"
            preview = ""
            if ok:
                preview = r.text[:200].replace("\n", " ")
            print(f"  {tag} {r.status_code:>3} {method} {path}  ({len(r.content)}b)  {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
