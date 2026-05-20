# DSP Pacing

Сервис мониторинга pace кампаний в Hybrid DSP. Тянет статистику через внутренний API кабинетов Hybrid'а (`console.hybrid.ai`, `console.selfclick.pro`, …), считает выполнение лимитов **за вчерашний день**, помечает кампании сигналом green/yellow/red. Зелёный = «вчера выполнила суточный И прогноз показывает выполнение плана за период → можно поднять техкост».

## Запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить (см. ниже)
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Открыть `http://127.0.0.1:8765/` — HTML страничка с цветной таблицей.

## .env (секреты, в `.gitignore`)

**Кабинет 1 (обязательный):**
- `HYBRID_HOST=console.hybrid.ai` — главный кабинет
- `HYBRID_AGENCY_LABEL=Hybrid` — короткий ярлык (показывается в UI префиксом `[Hybrid]`)
- `HYBRID_COOKIES="aft=...; csid=..."` — F12 → Application → Cookies → этого хоста
- `HYBRID_USER_ID=8902` — из URL-параметра `/core/login/ChangeAccount?userId=...&accountId=...`
- `HYBRID_AGENCY_IDS=...` — 24-hex id через запятую. Пусто = все доступные.

**Кабинет 2 (опциональный):**
- `HYBRID_HOST_2=console.selfclick.pro` — если пусто, второй tenant не подключается
- `HYBRID_AGENCY_LABEL_2=Selfclick`
- `HYBRID_COOKIES_2="aft=...; csid=..."`
- `HYBRID_USER_ID_2=17457`
- `HYBRID_AGENCY_IDS_2=` — пусто = все 13 agencies у Selfclick

**Прочее:**
- `AUTO_SYNC_TIME=03:00` — ежедневный авто-синк в это время локального времени (или `off`)
- `FETCH_CONCURRENCY=30` — параллельных запросов внутри agency
- `SKIP_ADVERTISER_NAME_REGEX` — regex для скипа явно мусорных advertiser'ов

**CRM (создание комиссий) — выключено по умолчанию:**
- `CRM_ENABLED=false` — модуль выключен (кнопок нет, endpoint'ы 403)
- `CRM_DRY_RUN=true` — включённый модуль только логирует, НЕ шлёт в CRM
- `CRM_HOST=newcrm.hybrid.ai`
- `CRM_COOKIES="aft=...; csid=..."` — куки от newcrm.hybrid.ai
- `CRM_TRADING_DESK_IDS=Hybrid:524bc2a4...,Selfclick:...` — TradingDeskId по кабинету

## Архитектура и важные нюансы

### Иерархия Hybrid

```
TradingDesk == кабинет (host)
  └── Agency  (от 13 до 1148 в зависимости от кабинета)
       └── Advertiser
            └── Campaign
```

Один host = один TD = свои куки = свой набор agencies. **`console.hybrid.ai`** — master-кабинет, видит 1148 agencies; **`console.selfclick.pro`** — отдельный TD на 13 agencies. Каждый — отдельный tenant в нашей конфигурации.

### Stateful сессия Hybrid

Сервер Hybrid'а помнит **активное агентство** в `csid` cookie. Чтобы посмотреть рекламодателей другого agency — нужно вызвать `GET /core/login/ChangeAccount?userId=X&accountId=Y`, и сервер обновит `csid` через `Set-Cookie`.

Из-за этого:
- **Нельзя параллельно вызывать ChangeAccount разных agency в одном клиенте** — `csid` гонится. Защита: `asyncio.Lock` на `_fetch_all_campaigns` в `app/main.py`.
- **Внутри одного agency** GetTotal'ы тащим параллельно через `asyncio.Semaphore(FETCH_CONCURRENCY=30)`.
- **Разные tenant'ы** — это разные httpx-клиенты с разными cookie jar, друг другу csid не ломают, но всё равно ходят последовательно tenant-за-tenant'ом (sequential по agencies внутри tenants).

### Endpoint'ы Hybrid (важные)

Все на `https://{host}`. Кука `aft` (auth) + `csid` (session, обновляется после ChangeAccount). Header `X-Requested-With: XMLHttpRequest`.

| Endpoint | Что | Заметки |
|---|---|---|
| `GET /core/account/GetSelfAccounts` | Все agencies/advertisers доступные user'у | Возвращает массив `{id, name, type, inventoryId, roles}`. Привязки к TD НЕТ. |
| `GET /core/login/ChangeAccount?userId=X&accountId=Y` | Переключить активное agency | Возвращает 302, новый `csid` в `Set-Cookie`. |
| `GET /core/advertisers/GetAll` | Рекламодатели **активного** agency | После ChangeAccount возвращает только его advertiser'ов. |
| `POST /core/agencyStatistic/GetTotal?advertiserId=...&startDate=...&endDate=...&campaignFilter=0&timeZoneId=305` | Кампании + статистика | Body: `{"fields":[2,4,58,60,59,43,1,76,77,62,61,7,6],"dynamicFields":[],...}`. |

**Lightweight endpoint'а только-кампаний без статистики нет** — фронт сам дёргает `GetTotal` чтобы показать список.

**Ретраи:** все запросы в `HybridClient._request` повторяются до 4 раз с паузами 0.5/1.5/3/6s на `httpx.TransportError` (ConnectTimeout/ReadTimeout/обрыв). Один сетевой чих не валит весь 7-минутный sync — у Hybrid'а сеть периодически штормит.

### КРИТИЧЕСКИЙ нюанс полей ответа `GetTotal`

Поля кампании в ответе ведут себя **не так как кажется по имени** (проверено эмпирически в `scripts/probe_lujo.py`):

| Поле | Что на самом деле | Зависит от окна? |
|---|---|---|
| `todaySum` / `todayImpressions` | Расход **за сегодня** (по серверу Hybrid'а) | НЕТ — константа |
| `totalPeriodSum` / `totalPeriodImpressions` | **Lifetime** — с самого старта кампании | НЕТ — константа |
| `totalSum` / `impressionCount` | Расход **за переданное окно** `startDate..endDate` | ДА — меняется |

То есть **наш «вчера»** = `totalSum` / `impressionCount` при запросе с окном `[вчера, вчера]`. **Не `totalPeriodSum`!** На этом был баг (см. `app/models.py` → `fact_for_unit` vs `lifetime_fact`).

### Pace-логика (`app/pacing.py`)

Запрос всегда за **вчера** (полный закрытый день — даёт честную картину для решения о техкосте).

```python
end = today - timedelta(days=1)  # вчера
start = end                       # окно = 1 день
```

Лимит выбирается по приоритету: `dailyMultiPriceLimitations` → `periodBudgetMultiPriceLimitations` → `totalMultiPriceLimitations`.

**`priceFormationType` определяет единицу:**
- `1` → **показы** (impressions) — сравниваем с todayImpressions/impressionCount
- `3` → **деньги** (валюта рекламодателя) — сравниваем с todaySum/totalSum
- `2` → клики (на практике не встречается)

**Две ключевые метрики:**

**`pace_yesterday`** = `yesterday_fact / daily_target`
Сколько % дневного лимита кампания выкрутила вчера. Это **основной сигнал для текущего решения** (поднимать ли техкост сейчас).

**`pace_overall`** (отображается как **«% прогноз»**) — прогноз выполнения плана за весь период `start..end`:
```python
planned_total   = daily_target * days_total       # сколько должно быть к концу
already_done    = lifetime_fact                   # с начала кампании по сегодня
daily_rate      = yesterday_fact                  # текущий темп (вчера)
projected_rest  = daily_rate * days_remaining     # допроект на оставшиеся дни
projected_total = already_done + projected_rest
pace_overall    = projected_total / planned_total
```

- `>= 1.0` — выполнит/перевыполнит план при текущем темпе
- `0.7–1.0` — немного не дотянет
- `< 0.7` — сильно не дотянет
- Для `isDontExpire` кампаний (без `end_date`) не считается → в UI пишет «нет даты».

**Решение GREEN** принимается на основе обеих метрик в `_decide_signal`. У кампаний без `end_date` смотрим только `pace_yesterday`. У кампаний с `end_date` — `pace_overall` (прогноз) как первоочередной.

### Stale-фильтрация во время sync

После compute_pace мы **отбрасываем** кампании которые не интересны для принятия решения:
- `status != 1` (paused/draft/etc) — `dropped["paused"]`
- `signal == FINISHED` — кампания уже закончилась
- `signal == NOT_STARTED`
- `signal == NO_LIMIT` — лимит не настроен
- `yesterday_fact <= 0` — вчера ничего не открутила (на паузе по факту)
- `end_date < завтра` — заканчивается сегодня, поднимать техкост бесполезно

В кэше остаются только активные green/yellow/red. UI это и показывает.

### Важно: НЕ фильтровать advertiser'ов по `balance`

Был баг — фильтровали `balance > 0` перед `GetTotal`. Сломалось: деньги могут быть перенесены с advertiser-баланса на саму кампанию, при этом `advertiser.balance=0`, но кампании активны. Сейчас фильтруем только по имени regex'ом (`for deleting`, `archive`, `test`).

## Производительность

- 17 agencies hybrid.ai + 13 selfclick.pro = **30 agencies**, full sync ~**7 минут**
- Жирные agencies (Cian.ru — 7500 advertiser'ов) превращали sync в 11+ минут — Ромчик их пока не включал в скоуп.
- Кэш `cache.json` в корне (в `.gitignore`) — снимок последнего sync'а. Грузится на старте, переписывается после каждого успешного sync'а. UI и `/cache/*` отдают мгновенно.

## Авто-синк и алерт кук

**Авто-синк:** на старте FastAPI запускается фоновый task (`_auto_sync_loop`), который спит до ближайшего `AUTO_SYNC_TIME` и вызывает `_run_sync_background()`. Без cron'а / launchd, всё внутри процесса.

**Алерт кук:** при первом 401/403 от Hybrid'а пишем в глобальный `_auth_health`, endpoint `GET /health/auth` отдаёт состояние. UI читает каждую минуту и при `ok: false` показывает красную плашку вверху страницы с указанием tenant'а и времени ошибки.

## CRM-интеграция (создание комиссий) — ИЗОЛИРОВАННЫЙ модуль

Сервис умеет создавать кастомные комиссии (техкост) на кампании в CRM
(`newcrm.hybrid.ai`). По кнопке в UI на 🟢 green-строке. По умолчанию **выключено**.

**Вся CRM-логика в одном файле `app/crm.py`.** Чтобы полностью вырезать:
1. удалить `app/crm.py`
2. убрать в `app/main.py` 2 строки с `crm` (импорт + `include_router`)
3. убрать в `app/static/index.html` блоки между `<!-- CRM:start -->` и `<!-- CRM:end -->`

**Два предохранителя:**
- `CRM_ENABLED=false` — endpoint'ы отдают 403, кнопок в UI нет
- `CRM_DRY_RUN=true` — включённый модуль только логирует payload, НЕ шлёт в CRM

**Endpoint CRM:** `POST https://newcrm.hybrid.ai/core/AgencyCommission/Create`
```json
{"CommissionEntity": {"AgencyId","AdvertiserId","FolderId":null,"CampaignId","TradingDeskId"},
 "Commission": {"Id":null,"CommissionScale":<int>,"DeviceType":null,"OS":null,
                "TrafficType":null,"BannerType":null,"Rewarded":null,"SspNumber":null}}
```

**Нюансы (выстраданные на боевом тесте):**
- `CommissionScale` — **только целое число** (int). Дробный float ломает десериализацию
  всего тела → 400 «model required + not valid integer». Шлём `int(round(scale))`.
- `CampaignId`/`AdvertiserId` совпадают с нашими 24-hex id из Hybrid'а.
- `TradingDeskId` — один на кабинет, маппится из `CRM_TRADING_DESK_IDS` по tenant'у.
- CRM **флапает 401** (csid короткоживущий/нестабилен). На 401 делаем авто-ретрай
  до 3 раз — это безопасно: 401 = «не авторизован» = комиссия точно не создалась,
  дубля не будет. На 5xx/таймаут НЕ ретраим (Create мог пройти → риск дубля).
- Каждая операция (dry-run и боевая) пишется в `commissions_log.json` (в `.gitignore`).

**Статус:** боевое создание проверено — HTTP 200, комиссия создаётся. Сейчас
модуль выключен флагом (`CRM_ENABLED=false`) «на всякий случай».

## Структура

```
app/
  config.py         # pydantic-settings, .env, TenantConfig, settings.tenants, CRM-настройки
  models.py         # HybridCampaign / HybridPriceLimit / CampaignPaceOut / SignalLevel
                    # currency_code/symbol, fact_for_unit, lifetime_fact
  hybrid_client.py  # async HTTP-клиент: list_agencies/switch_to_agency/
                    # list_advertisers/get_total. _request с ретраями. make_client_for(tenant).
  pacing.py         # compute_pace + _decide_signal (yesterday + forecast)
  main.py           # FastAPI app, lock, cache, multi-tenant, auto-sync,
                    # /health/auth, /sync/start, /sync/status
  crm.py            # ИЗОЛИРОВАННЫЙ CRM-модуль: APIRouter /crm/*, создание комиссий
  static/
    index.html      # UI (vanilla JS + CSS): таблица, фильтры, sync progress,
                    # auth alert, цветные сигналы, pace badges, CRM-кнопка+модалка
scripts/            # одноразовые probes — auth, hierarchy, конкретные кампании,
                    # match_agencies (поиск agency_id по списку имён)
```

## Endpoint'ы FastAPI

- `GET /` — HTML страничка
- `GET /cache/summary` — сводка по сигналам (мгновенно из памяти)
- `GET /cache/green` — только зелёные
- `GET /cache/all?signal=` — всё из кэша (используется UI)
- `GET /agencies?only_named=true&tenant=` — список agencies всех кабинетов (или одного)
- `GET /advertisers?agency_id=&tenant=` — рекламодатели agency
- `POST /sync/start` — запускает фоновый sync (под глобальным `_sync_lock`)
- `GET /sync/status` — прогресс текущего/последнего sync'а (для прогресс-бара UI)
- `GET /summary` ⚠️ — блокирующий sync (~7 мин)
- `GET /campaigns?signal=&only_active=` — то же, массивом
- `GET /campaigns/green` — зелёные через блокирующий sync
- `GET /health/auth` — статус кук (UI читает, чтобы показать плашку)
- `POST /crm/commission` — создать комиссию (403 если CRM_ENABLED=false)
- `GET /crm/status` — состояние CRM-модуля (enabled/dry_run/td-map)
- `GET /crm/log` — история созданных комиссий
- `GET /docs` — Swagger

## TODO (что обсуждалось, не сделано)

1. **История pace за N дней** — копить snapshots по дням, показывать «🟢🟢🟢 стабильно N дней» как дополнительный сигнал.
2. **«Отметить как обработано»** — чекбоксы в UI чтоб помечать кампании на которых техкост уже подняли (с фильтром «скрыть обработанные»).
3. **Экспорт в CSV** — выгрузка результатов sync'а.
4. **Telegram бот** — пуш зелёных по расписанию.
5. **Postgres** — переезд кэша/истории из файла в БД.
6. **Smart cache** — incremental sync только по advertiser'ам с кампаниями, full раз в сутки. Актуально если когда-нибудь добавим Cian.ru-подобные жирные agencies.
7. **CRM: защита от дублей** — повторное создание комиссии на одной кампании может
   плодить дубли (поведение CRM при повторе не проверено). Можно проверять
   `commissions_log.json` и предупреждать «уже создавали».
8. **CRM: TradingDeskId для Selfclick** — пока в `CRM_TRADING_DESK_IDS` только Hybrid.
   На selfclick-кампаниях создание комиссии выдаст ошибку, пока не добавлен td_id.

## История ключевых решений

- **Окно запроса = вчера** (не MTD, не сегодня). Сегодня не закончилось — нет полной статистики. Вчера — единственный валидный day для решения о техкосте.
- **`totalSum` ≠ lifetime** — в API Hybrid'а это window-fact, а lifetime сидит в `totalPeriodSum`. Перепутывание давало pace_yesterday в 200x от реальности.
- **`pace_overall` = forecast**, а не lifetime-average. Историческое среднее (как было раньше) не реагирует на текущие изменения и бесполезно для решения. Forecast — «выполнит ли план до end_date при текущем темпе» — это и нужно.
- **Multi-tenant через `_2` суффикс** в .env. Просто, без yaml. Когда понадобится 3+ кабинета — обобщим.
- **Auto-sync через asyncio task** в lifespan'е, без внешних зависимостей. Запускается каждые сутки в `AUTO_SYNC_TIME`.
- **CRM изолирована в одном файле** + 2 флага. Ромчик попросил: «вдруг наебнём — должно легко вырезаться». Поэтому `app/crm.py` самодостаточен, точки подключения помечены комментариями.
- **CommissionScale только int** — CRM .NET-бэк не принимает float, ломается десериализация всего тела.
- **Ретрай CRM только на 401** — 401 гарантированно не создал комиссию, ретрай безопасен. На 5xx/таймаут не ретраим — Create мог пройти, был бы дубль.

## Контекст пользователя (Ромчик)

Общение неформальное, по-братски, можно с матом — Ромчику так комфортнее (см. memory). На код матерись свободно. На самого Ромчика — нет.
