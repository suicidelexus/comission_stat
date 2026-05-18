# DSP Pacing

Сервис мониторинга pace кампаний в Hybrid DSP. Тянет статистику через внутренний API кабинета `console.hybrid.ai`, считает выполнение лимитов по дням месяца (MTD), помечает кампании сигналом green/yellow/red. Зелёный = «pace ≥ 1.0, кампания сегодня крутит, есть запас по времени → можно поднять техкост».

## Запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить (см. ниже)
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Открыть `http://127.0.0.1:8765/` — HTML страничка с цветной таблицей.

## .env (секреты, в `.gitignore`)

- `HYBRID_HOST=console.hybrid.ai` — главный кабинет, **видит все agencies сразу**
- `HYBRID_COOKIES="aft=...; csid=..."` — берутся в браузере: F12 → Application → Cookies → `console.hybrid.ai`
- `HYBRID_USER_ID=8902` — извлекается из URL-параметра запроса `/core/login/ChangeAccount?userId=...&accountId=...`
- `HYBRID_AGENCY_IDS=ag1,ag2,...` — список 24-hex id агентств. Список «всех доступных» получается через `GET /agencies?only_named=true` после старта.

## Архитектура и важные нюансы

### Иерархия Hybrid

```
TradingDesk (кабинет — example: console.hybrid.ai)
  └── Agency  (1148 доступных в master-кабинете hybrid.ai)
       └── Advertiser (от 1 до 7500+ в агентстве, например Cian.ru)
            └── Campaign
```

Все статусы и метрики живут на уровне Campaign. Pace считаем по кампаниям.

### Stateful сессия Hybrid

Это критично. Сервер Hybrid'а помнит **активное агентство** в `csid` cookie. Чтобы посмотреть кампании другого агентства — нужно вызвать `GET /core/login/ChangeAccount?userId=X&accountId=Y`, и сервер обновит `csid` через `Set-Cookie`.

Из-за этого:
- **Нельзя параллельно вызывать ChangeAccount разных агентств** в одном клиенте — `csid` гонится. У нас уже был баг с двумя параллельными `/summary` запросами, видно в логах.
- Защита: `asyncio.Lock` на `_fetch_all_campaigns` в `app/main.py` — только один sync одновременно.
- **Внутри одного агентства** можно параллелить `GetTotal` (он не меняет `csid`) — это делается через `asyncio.Semaphore(settings.fetch_concurrency)`.

### Найденные endpoint'ы Hybrid

Все на `https://console.hybrid.ai`. Кука `aft` (auth) + `csid` (session). Header `X-Requested-With: XMLHttpRequest`.

| Endpoint | Что | Заметки |
|---|---|---|
| `GET /core/account/GetSelfAccounts` | Все agencies/advertisers/td доступные user'у | 1248 записей у Ромчика. Поле `type`: "Agency" \| "Advertiser". |
| `GET /core/login/ChangeAccount?userId=X&accountId=Y` | Переключить активное agency в сессии | Возвращает 302, новый `csid` в `Set-Cookie`. |
| `GET /core/advertisers/GetAll` | Рекламодатели **активного** agency | Поля: id, name, balance, currency, domain. |
| `POST /core/agencyStatistic/GetTotal?advertiserId=...&startDate=...&endDate=...&campaignFilter=0&timeZoneId=305` | Список кампаний + статистика | Body: `{"fields":[2,4,58,60,59,43,1,76,77,62,61,7,6],"dynamicFields":[],...}`. Возвращает `campaigns[]` со всеми лимитами и фактами. |

Lightweight endpoint'а только-кампаний без статистики у Hybrid **нет** — фронт сам дёргает `GetTotal` чтобы показать список.

### Pace-логика (`app/pacing.py`)

Окно: **MTD** — с 1 числа текущего месяца по сегодня (`start = today.replace(day=1)`).

Лимит выбирается в порядке приоритета: `dailyMultiPriceLimitations` → `periodBudgetMultiPriceLimitations` → `totalMultiPriceLimitations`.

**`priceFormationType` определяет единицу:**
- `1` → **показы** (impressions). Сравниваем с `todayImpressions` / `totalPeriodImpressions`.
- `3` → **рубли**. Сравниваем с `todaySum` / `totalPeriodSum`.
- `2` → клики (не встречал в данных Ромчика, но поддерживается).

**Pace:**
```
daily_target = limit.amount  (если daily)
             | period_budget / days_total  (если period budget)
pace_today   = today_fact / daily_target
pace_overall = period_fact / (daily_target × days_passed_in_window)
```

**GREEN** = `pace_overall >= 1.0` И `today_fact > 0` (кампания крутит сегодня, не на паузе) И есть запас по `days_left` (≥3 дня или `isDontExpire`).

### Status кодов кампании

- `1` — активна
- `2` — на паузе (deal у Ромчика — точное значение неизвестно, но `today_fact=0` подтверждает что не крутит)
- остальные не встречали

В UI фильтр `only_active` отсекает всё кроме `status=1`.

### Важно: НЕ фильтровать по `advertiser.balance`

Был баг — фильтровали `balance > 0` перед `GetTotal`. **Сломалось**: деньги могут быть перенесены с advertiser-баланса на саму кампанию, при этом `advertiser.balance=0`, но кампании активны. Зелёные кампании пропадали.

Сейчас фильтруем только по имени (`for deleting`, `archive`, `test`) — это явный мусор.

## Производительность

Первый full sync по 29 агентствам ~**11 минут** (там Cian.ru с 7522 advertiser'ами). Распределение времени:
- Cian.ru сам по себе ~9 минут (7522 advertiser'а × ~0.07 сек/запрос с semaphore=30).
- Остальные 28 агентств ~2 минуты в сумме.

**Результат full sync'а (на момент написания):** 24 875 кампаний, из них:
- 🟢 green: 216
- 🟡 yellow: 128
- 🔴 red: 3 982
- ⚫ finished: 20 542
- ⚪ no_limit: 7

Из 8000+ advertiser'ов с кампаниями в мае было **4302**. Это и есть «white list» для smart cache (см. ниже TODO).

## Кэш

`cache.json` в корне (в `.gitignore`). Полный snapshot последнего sync'а — `{at, items}`. Загружается на старте в lifespan, сохраняется после каждого успешного sync'а. Это позволяет:
- Перезапустить сервис — UI сразу работает с прежним кэшем.
- Endpoint'ы `/cache/*` отдают данные мгновенно.

Lock-протекция на `/summary`, `/campaigns`, `/campaigns/green` — один блокирующий sync (11 мин). Юзеру стоит дёргать только `/cache/*`.

## Структура

```
app/
  config.py        # pydantic-settings, читает .env
  models.py        # HybridCampaign + CampaignPaceOut + SignalLevel
  hybrid_client.py # async HTTP-клиент: list_agencies, switch_to_agency, list_advertisers, get_total
  pacing.py        # compute_pace + _decide_signal
  main.py          # FastAPI app, lock, cache, endpoints
  static/
    index.html     # UI (vanilla JS + CSS, без сборки)
scripts/           # одноразовые probes (probe_auth.py, probe_change_account.py, find_agencies.py)
```

## Endpoint'ы

- `GET /` — HTML страничка
- `GET /cache/summary` — сводка из кэша (мгновенно)
- `GET /cache/green` — зелёные из кэша
- `GET /cache/all?signal=&only_active=` — всё из кэша (для UI)
- `GET /agencies?only_named=true` — список 1148 agencies (для выбора agency_id в `.env`)
- `GET /advertisers?agency_id=X` — рекламодатели агентства
- `GET /summary` ⚠️ — пере-синкает (11 мин), используется для refresh
- `GET /campaigns?signal=&only_active=` — то же что summary, возвращает массив
- `GET /campaigns/green` — зелёные через пере-синк (не нужно, дёргай `/cache/green`)
- `GET /docs` — Swagger

## TODO

1. **Smart cache + cron loop** — раз в 15-30 минут sync только по advertiser'ам у которых были кампании в этом месяце (~500 вместо 8000) = ~1-2 минуты вместо 11. Раз в 6 часов full refresh для новых advertiser'ов.
2. **Postgres + история pace** — копить snapshots, считать «стабильно green N дней подряд» (изначальный замысел).
3. **Обновление кук** — когда `aft`/`csid` протухнут, текущая логика кинет 401 в `/cache/*` пустоту не отдаёт. Нужен алерт «куки протухли, обнови в .env».
4. **Telegram бот** — был выкинут из MVP, но как опция.

## История ключевых решений

- **Кабинет hybrid.ai** вместо отдельных белых лейблов (artics.ru, hybrid.ru, ...) — из master-кабинета `console.hybrid.ai` видны все 1148 агентств. Один host = одни куки = много agencies.
- **MTD окно** — Ромчик попросил период «с 1 числа текущего месяца по сегодня». В коде: `end = today; start = today.replace(day=1)`.
- **Не фильтруем balance** — баланс может быть на кампании, не у рекламодателя.
- **GREEN требует today_fact > 0** — иначе любая старая хорошая кампания на паузе светилась зелёной.

## Контекст пользователя (Ромчик)

Общение неформальное, по-братски, можно с матом — Ромчику так комфортнее (см. memory). На код матерись свободно. На самого Ромчика — нет.
