"""Проверка: умеет ли ChangeAccount действительно переключать активное агентство."""
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

    # из curl мы знаем что userId=8902 (это userId Ромчика)
    USER_ID = "8902"

    # берём первое не-пустое имя из списка агентств — попробуем туда переключиться
    self_accounts = json.loads(Path("tmp_self_accounts.json").read_text())
    target = None
    for a in self_accounts:
        if a.get("type") == "Agency" and a.get("name") and a["name"].strip() and a["name"].strip() != "ADShark":
            target = a
            break
    if not target:
        target = next(a for a in self_accounts if a.get("type") == "Agency")
    print(f"target agency: {target['id']}  name={target.get('name')!r}")

    # парсим куки из env в cookies jar httpx
    cookies_header = env["HYBRID_COOKIES"]
    cookies: dict[str, str] = {}
    for pair in cookies_header.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookies[k.strip()] = v.strip()

    base_headers = {
        "Referer": f"https://{host}/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
    }

    with httpx.Client(
        timeout=20.0,
        cookies=cookies,
        follow_redirects=False,
    ) as client:
        # 1. до переключения — какие advertiser'ы видим
        r0 = client.get(
            f"https://{host}/core/advertisers/GetAll",
            headers={
                **base_headers,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        print(f"\nBEFORE ChangeAccount → advertisers/GetAll: {r0.status_code} {len(r0.content)}b")
        if r0.status_code == 200 and "json" in r0.headers.get("content-type", ""):
            advs_before = r0.json()
            print(f"  advertisers count: {len(advs_before)}")
            for a in advs_before:
                print(f"    {a['id']}  {a['name']!r}")

        # 2. ChangeAccount
        change_url = f"https://{host}/core/login/ChangeAccount"
        print(f"\n--- ChangeAccount → userId={USER_ID} accountId={target['id']} ---")
        r1 = client.get(
            change_url,
            params={"userId": USER_ID, "accountId": target["id"]},
            headers={
                **base_headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        print(f"  status: {r1.status_code}")
        print(f"  set-cookie headers: {len(r1.headers.get_list('set-cookie'))}")
        for c in r1.headers.get_list("set-cookie"):
            print(f"    {c[:120]}")
        if r1.status_code in (301, 302, 303, 307, 308):
            print(f"  Location: {r1.headers.get('Location')}")
        # пройдём по редиректу если нужно
        if r1.status_code in (301, 302, 303, 307, 308):
            r1b = client.get(r1.headers["Location"] if r1.headers["Location"].startswith("http") else f"https://{host}{r1.headers['Location']}", headers=base_headers)
            print(f"  follow redirect: {r1b.status_code}")
            for c in r1b.headers.get_list("set-cookie"):
                print(f"    {c[:120]}")

        print(f"\n  client cookies now: {list(client.cookies.keys())}")
        print(f"  aft prefix: {client.cookies.get('aft','')[:40]}…")

        # 3. после переключения — какие advertiser'ы
        r2 = client.get(
            f"https://{host}/core/advertisers/GetAll",
            headers={
                **base_headers,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        print(f"\nAFTER ChangeAccount → advertisers/GetAll: {r2.status_code} {len(r2.content)}b")
        if r2.status_code == 200 and "json" in r2.headers.get("content-type", ""):
            advs_after = r2.json()
            print(f"  advertisers count: {len(advs_after)}")
            for a in advs_after[:10]:
                print(f"    {a['id']}  {a['name']!r}")
        else:
            print(r2.text[:500])

    return 0


if __name__ == "__main__":
    sys.exit(main())
