"""Прозвонить куки selfclick.pro и посмотреть GetSelfAccounts.
Это другой TD (646c6334db72ea07f0337d01) — должен вернуть свой набор agencies."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

HOST = "console.selfclick.pro"
COOKIES = (
    "aft=CfDJ8P_zUl1Hbb1Ko1BXsCUg_mJ849S8kxGLY3Sl3NfgsY3_hV4YOwt_fVKE687nKcFlM1hbUg9y4GBdy3np2kNyO89rJY9PYvjdwNTXmjfQxMFTnOrWMKmW-9iwHQy7051x2C4DTmm0TNkObTxKuRkr_oc; "
    "csid=CfDJ8P_zUl1Hbb1Ko1BXsCUg_mI0S5-m9sXfnbguLj9VDe9d1TLNHfyGib3aO535hiLnEDSy4gijG8jl1U3kU3nijsD4985QJFgvjuG_TdSHI_Bb7BjYjlitW55VU2cUmhn7g-YsgmOWQd4OCcfDlCcysc38HPSfdtIsv1zmUh1AIBH-QziQ_OkgADmHJkxXEcZA3vo1y1E72vcmO6BJXlkBfGtv_xFe30gk0sOgMqgMQpIsxDBXPOR76dvU-Vm0_VzlEHApgaNAilkN8pIDRBeu-fl8BN4lklG6CHWN-WI86n9mjTWbUfXLD93gxbMn-yXdskDXDfCWwmg_ACfXf9VmH5YJ7T18bInsb0hj89mHTik_n0xgl_j4E-2V1i7ZU1N4OEvhSIrnLlqUuOLOJhfZJKMtYqOiA_Ci-UQQbnAVOL7a"
)


def main() -> int:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://{HOST}/",
        "User-Agent": "Mozilla/5.0",
        "Cookie": COOKIES,
    }
    with httpx.Client(timeout=20.0, follow_redirects=False) as c:
        r = c.get(f"https://{HOST}/core/account/GetSelfAccounts", headers=headers)
    print(f"status: {r.status_code}  ctype: {r.headers.get('content-type')}  size: {len(r.content)}b")
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        print(r.text[:500])
        return 1

    data = r.json()
    out = Path(__file__).resolve().parent.parent / "tmp_self_accounts_selfclick.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"saved → {out.name}")

    from collections import Counter
    types = Counter(a.get("type") for a in data)
    print(f"\ntotal: {len(data)}")
    print(f"types: {dict(types)}")
    print(f"\nfirst 15 named agencies:")
    shown = 0
    for a in data:
        if a.get("type") == "Agency" and a.get("name", "").strip():
            print(f"  {a['id']}  '{a['name']}'")
            shown += 1
            if shown >= 15:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
