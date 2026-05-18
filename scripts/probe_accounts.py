"""Прозвон GetSelfAccounts и advertisers/GetAll."""
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
    headers_json = {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://{host}/",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookies,
    }
    headers_xhr = {
        **headers_json,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    out_dir = Path(__file__).resolve().parent.parent
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        # 1. список агентств
        r1 = client.get(f"https://{host}/core/account/GetSelfAccounts", headers=headers_json)
        print(f"GetSelfAccounts: {r1.status_code} {r1.headers.get('content-type')} {len(r1.content)}b")
        if r1.status_code == 200 and "json" in r1.headers.get("content-type", ""):
            d1 = r1.json()
            (out_dir / "tmp_self_accounts.json").write_text(
                json.dumps(d1, ensure_ascii=False, indent=2)
            )
            print("  saved → tmp_self_accounts.json")
            # покажем структуру
            if isinstance(d1, list):
                print(f"  list with {len(d1)} items, first item keys:")
                if d1:
                    print(f"    {list(d1[0].keys())[:20]}")
                    print(f"  sample: {json.dumps(d1[0], ensure_ascii=False)[:500]}")
            elif isinstance(d1, dict):
                print(f"  dict keys: {list(d1.keys())[:20]}")
                print(f"  sample: {json.dumps(d1, ensure_ascii=False)[:500]}")
        else:
            print(r1.text[:500])

        print()

        # 2. список рекламодателей (текущего активного account'а)
        r2 = client.get(f"https://{host}/core/advertisers/GetAll", headers=headers_xhr)
        print(f"advertisers/GetAll: {r2.status_code} {r2.headers.get('content-type')} {len(r2.content)}b")
        if r2.status_code == 200 and "json" in r2.headers.get("content-type", ""):
            d2 = r2.json()
            (out_dir / "tmp_advertisers.json").write_text(
                json.dumps(d2, ensure_ascii=False, indent=2)
            )
            print("  saved → tmp_advertisers.json")
            if isinstance(d2, list):
                print(f"  list with {len(d2)} items, first item keys:")
                if d2:
                    print(f"    {list(d2[0].keys())[:20]}")
                    print(f"  sample: {json.dumps(d2[0], ensure_ascii=False)[:500]}")
            elif isinstance(d2, dict):
                print(f"  dict keys: {list(d2.keys())[:20]}")
                print(f"  sample: {json.dumps(d2, ensure_ascii=False)[:600]}")
        else:
            print(r2.text[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
