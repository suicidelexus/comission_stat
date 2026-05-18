"""Найти agency_id по списку имён."""
from __future__ import annotations
import json, sys
from pathlib import Path

WANT = [
    "E-promo International RU",
    "E-promo International",
    "E-promo",
    'MI10 // "Уайт бокс медиа"',
    "Cian.ru",
    "Cian click-out",
    "Programmatic.ru // Бизнес солюшнс",
    "Digital Alliance WL",
    "BuyingPower",
    "Tinkoff Client",
    "Iqueem Digital Marketing Agency",
    "Додо Пицца",
    "I-Com",
    "NLC",
    "Media Pulse // Эд контакт",
    "Лайм Медиа",
    "ОККАМ",
    "InGate",
    "Bring Ads",
    "OMD",
    "Genius Group",
    "FirstData",
    "Megafon White Label",
]


def norm(s: str) -> str:
    return " ".join(s.lower().replace("«", '"').replace("»", '"').replace("ё", "е").split())


def main() -> int:
    data_path = Path(__file__).resolve().parent.parent / "tmp_self_accounts.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    agencies = [a for a in data if a.get("type") == "Agency"]
    by_norm: dict[str, list[dict]] = {}
    for a in agencies:
        by_norm.setdefault(norm(a.get("name", "")), []).append(a)

    found: list[tuple[str, str, str]] = []
    missing: list[str] = []
    ambiguous: list[tuple[str, list[dict]]] = []
    for name in WANT:
        key = norm(name)
        hits = by_norm.get(key, [])
        if not hits:
            substr = [a for a in agencies if key in norm(a.get("name", ""))]
            if len(substr) == 1:
                a = substr[0]
                found.append((name, a["id"], a["name"]))
            elif len(substr) > 1:
                ambiguous.append((name, substr))
            else:
                missing.append(name)
        elif len(hits) == 1:
            a = hits[0]
            found.append((name, a["id"], a["name"]))
        else:
            ambiguous.append((name, hits))

    print(f"=== FOUND ({len(found)}) ===")
    for want, aid, actual in found:
        marker = "" if want.strip().lower() == actual.strip().lower() else f"  (matched: '{actual}')"
        print(f"  {aid}  {want}{marker}")

    if ambiguous:
        print(f"\n=== AMBIGUOUS ({len(ambiguous)}) ===")
        for want, hits in ambiguous:
            print(f"  {want!r} → {len(hits)} matches:")
            for h in hits:
                print(f"      {h['id']}  '{h['name']}'")

    if missing:
        print(f"\n=== MISSING ({len(missing)}) ===")
        for name in missing:
            print(f"  {name!r}")
            # суггест по подстроке последнего слова
            tail = norm(name).split()[-1]
            sugg = [a for a in agencies if tail in norm(a.get("name", ""))][:5]
            for s in sugg:
                print(f"      hint: {s['id']}  '{s['name']}'")

    if not ambiguous and not missing:
        ids = ",".join(aid for _, aid, _ in found)
        print(f"\n=== HYBRID_AGENCY_IDS ===\n{ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
