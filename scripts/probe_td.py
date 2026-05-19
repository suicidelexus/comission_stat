"""Проверяем: можно ли через ChangeAccount(TD_id) попасть в контекст TD
и получить его список agencies."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.hybrid_client import make_client


TD_ID = "646c6334db72ea07f0337d01"


async def main():
    client = make_client()

    # 1. до переключения — сколько agency видим
    accs_before = await client.list_self_accounts()
    print(f"BEFORE: {len(accs_before)} total, {sum(1 for a in accs_before if a.type=='Agency')} agencies")

    # 2. пробуем ChangeAccount на TD id
    try:
        await client.switch_to_agency(TD_ID)
        print(f"\nChangeAccount({TD_ID}) → OK")
    except Exception as e:
        print(f"\nChangeAccount({TD_ID}) → ERR: {e}")
        await client.close()
        return

    # 3. после переключения — другой список?
    accs_after = await client.list_self_accounts()
    print(f"AFTER:  {len(accs_after)} total, {sum(1 for a in accs_after if a.type=='Agency')} agencies")

    # 4. и список advertisers (рекламодатели "TD" — может быть его agencies?)
    try:
        advs = await client.list_advertisers()
        print(f"\nadvertisers/GetAll after switch: {len(advs)}")
        for a in advs[:10]:
            print(f"  {a.id}  '{a.name}'")
    except Exception as e:
        print(f"advertisers/GetAll failed: {e}")

    # 5. diff списка agencies — могло появиться что-то новое
    before_ids = {a.id for a in accs_before if a.type == "Agency"}
    after_ids = {a.id for a in accs_after if a.type == "Agency"}
    added = after_ids - before_ids
    removed = before_ids - after_ids
    print(f"\ndiff: +{len(added)} -{len(removed)} agencies in list")

    await client.close()


asyncio.run(main())
