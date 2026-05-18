"""Прозваниваем куки на простом эндпоинте справочника. Если 200 + JSON — куки живые."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx


def load_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        print(f"[!] no .env at {env_path}", file=sys.stderr)
        sys.exit(2)
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env = load_env()
    host = env.get("HYBRID_HOST") or os.environ.get("HYBRID_HOST")
    cookies_header = env.get("HYBRID_COOKIES") or os.environ.get("HYBRID_COOKIES")
    if not host or not cookies_header:
        print("[!] need HYBRID_HOST and HYBRID_COOKIES", file=sys.stderr)
        return 2

    # тот же URL что Ромчик дал — справочник федеральных округов России
    country_id = "535d61c000006400020000c1"
    url = f"https://{host}/core/geo/GetFederalRegionsByCountry"

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://{host}/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        "Cookie": cookies_header,
    }

    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        r = client.get(url, params={"countryId": country_id}, headers=headers)

    print(f"status: {r.status_code}")
    print(f"content-type: {r.headers.get('content-type')}")
    print(f"length: {len(r.content)} bytes")

    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and "json" in ctype:
        try:
            data = r.json()
        except Exception as e:
            print(f"[!] not JSON despite content-type: {e}")
            print(r.text[:500])
            return 1
        if isinstance(data, list):
            print(f"[ok] got JSON array, {len(data)} items")
            for item in data[:3]:
                print(f"    sample: {item}")
        else:
            print("[ok] got JSON object")
            print(f"    keys: {list(data.keys())[:10]}")
        return 0

    print("[!] looks like auth failed or wrong content")
    print(r.text[:800])
    return 1


if __name__ == "__main__":
    sys.exit(main())
