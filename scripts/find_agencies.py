"""Поиск agency по списку названий — fuzzy substring match."""
from __future__ import annotations

import json
import re
from pathlib import Path

WANTED = [
    "Cian.ru",
    "Cian click-out",
    "Iqueem Digital Marketing Agency",
    "E-promo",
    "E-promo International RU",
    'Mi10 // "Уайт Бокс Медиа"',
    "Додо Пицца",
    "I-Com",
    "NLC",
    'Media Pulse // ООО "ЭД контакт"',
    "Лайм Медиа",
    "Media Pulse",
    "ОККАМ",
    "InGate",
    "Bring Ads",
    "OMD",
    "Buying Power",
    "Programmatic.ru",
    "E-Promo International",
    "Genius Group",
    "FirstData",
    "Moko-marketing",
    "VBI",
    "RTA",
    "OZON",
    "Tinkoff Client",
    "Точка Банк",
    "Artics // Rocketbank",
    "Купер",
]


def normalize(s: str) -> str:
    """lower + strip + collapse whitespace."""
    return re.sub(r"\s+", " ", s.strip().lower())


def main() -> int:
    accs = json.loads(Path("tmp_self_accounts.json").read_text())
    agencies = [a for a in accs if a.get("type") == "Agency"]

    # индекс: normalized name → list of accounts
    index: dict[str, list] = {}
    for a in agencies:
        n = normalize(a.get("name", ""))
        if n:
            index.setdefault(n, []).append(a)

    matches: list[tuple[str, list]] = []
    misses: list[str] = []

    for wanted in WANTED:
        wn = normalize(wanted)
        # 1. точный матч
        if wn in index:
            matches.append((wanted, index[wn]))
            continue
        # 2. substring обоих направлений
        candidates = []
        for a in agencies:
            an = normalize(a.get("name", ""))
            if not an:
                continue
            if wn in an or an in wn:
                candidates.append(a)
        if candidates:
            matches.append((wanted, candidates))
        else:
            misses.append(wanted)

    print("=== MATCHES ===")
    for wanted, cands in matches:
        if len(cands) == 1:
            a = cands[0]
            print(f"  {wanted!r}")
            print(f"    → {a['id']}  name={a['name']!r}")
        else:
            print(f"  {wanted!r}  → {len(cands)} candidates:")
            for a in cands[:5]:
                print(f"    {a['id']}  name={a['name']!r}")

    print()
    print("=== MISSES ===")
    for w in misses:
        print(f"  {w!r}")

    print()
    print("=== UNAMBIGUOUS (1 candidate) — готово копировать в .env ===")
    ids = []
    for wanted, cands in matches:
        if len(cands) == 1:
            ids.append(cands[0]["id"])
    print(",".join(ids))
    print(f"\ntotal unambiguous: {len(ids)} / {len(WANTED)}")
    return 0


if __name__ == "__main__":
    main()
