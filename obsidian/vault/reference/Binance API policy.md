# Binance API — политики и лимиты, аудит нашего бота

Составлено 2026-08-24 после двух банов IP (23.08 и 24.08). Всё, что ниже, —
из официальных источников Binance, с проверкой на нашем коде.

Источники:
- [REST API · Limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits)
- [rest-api.md (GitHub)](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md)
- [WebSocket API · Rate limits](https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/rate-limits)
- [Market Data Only](https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only)
- [Academy: How to Avoid Getting Banned by Rate Limits](https://www.binance.com/en/academy/articles/how-to-avoid-getting-banned-by-rate-limits)
- [Terms of Use](https://www.binance.com/en/terms) (раздел III.1.b.iv)
- [How to Use an API Key Securely](https://www.binance.com/en/blog/security/how-to-use-an-api-key-securely-5-tips-from-binance-8638066848800196896)
- [Railway · Static Outbound IPs](https://docs.railway.com/networking/static-outbound-ips)

---

## 1. Лимиты — точные цифры

Взяты живьём из `GET /api/v3/exchangeInfo → rateLimits` (24.08.2026):

| Тип | Интервал | Лимит |
|---|---|---|
| `REQUEST_WEIGHT` | 1 минута | **6000** |
| `ORDERS` | 10 секунд | 100 |
| `ORDERS` | 1 день | 200 000 |
| `RAW_REQUESTS` | 5 минут | 300 000 |

**Лимиты считаются по IP, а не по ключу.** Все аккаунты с одного адреса делят
один бюджет. Ордерные лимиты — наоборот, по аккаунту.

Вес эндпоинтов, которые мы трогаем:

| Эндпоинт | Вес | Сколько раз за цикл |
|---|---|---|
| `/api/v3/ping` | 1 | 1 (новый прелёт) |
| `/api/v3/exchangeInfo` | 20 | было 4, стало **1** |
| `/api/v3/klines` | 2 | 4 |
| `/api/v3/account` | 20 | 1 (кэш на цикл, сбрасывается после сделки) |
| `POST /api/v3/order` | 1 | 0–4 |

**Наш цикл: было ~110, стало ~38 единиц веса раз в 4 часа.** Бюджет — 6000 в
минуту. То есть цикл целиком = **0.6% бюджета ОДНОЙ минуты**.

## 2. 429 и 418 — как это работает на самом деле

Дословно из документации:

> «Requests fail with HTTP status code 429 when you exceed the request rate limit.»

> «Repeatedly violating rate limits and/or failing to back off after receiving
> 429s will result in an automated IP ban (HTTP status 418). IP bans are tracked
> and **scale in duration for repeat offenders, from 2 minutes to 3 days**.»

> «When a 429 is received, it's your obligation as an API to **back off and not
> spam the API**.»

> «A `Retry-After` header is sent with a 418 or 429 response and will give the
> number of seconds required to wait, in the case of a 429, to prevent a ban,
> or, in the case of a 418, until the ban is over.»

**Ключевое, что мы упустили: 418 выдаётся не за превышение веса, а за то, что
после 429 продолжают слать запросы.** У нас 22.08 в 20:01 прилетел 429 —
и `_retry` отправил ещё два запроса. Это ровно то поведение, которое Binance
наказывает баном.

## 3. Что Binance предписывает делать

| Требование | Было у нас | Стало |
|---|---|---|
| Читать `X-MBX-USED-WEIGHT-1M` и следить за давлением | **не читали вообще** | пишем пик за цикл, орём если > 300 |
| На 429 — экспоненциальный откат, не спамить | слали ещё 2 запроса | ни одного запроса в бан |
| На 418 — ждать до `Retry-After` / метки в теле | игнорировали | ждём, берём **более дальний** из двух сроков |
| Батчить запросы | 4 × `exchangeInfo` | 1 × `exchangeInfo` на всю корзину |
| Держать `limit` в списках маленьким | `klines` 390 → вес 2 | без изменений, уже минимум |
| WebSocket вместо поллинга для live-данных | REST | **не нужно**: цикл раз в 4 часа, а не стрим |

## 4. Почему банят именно нас — и это не наш трафик

[Документация Railway про статические исходящие IP](https://docs.railway.com/networking/static-outbound-ips):

> «There is no guarantee that the IPv4 addresses assigned to your service are
> dedicated. **They may be shared with other customers.**»

Плюс HA-режим раздаёт **три** адреса с балансировкой. Сосед по адресу гоняет
Binance → банят адрес → «all connections from a banned IP address are blocked,
affecting all accounts using that IP».

Проверенный факт: оба раза бан **уже действовал** на первом же нашем запросе
(осталось 40:52 и 41:23). Мы физически не могли выбрать 6000/мин, тратя 110 за
четыре часа.

### Проверено и НЕ работает как обход

`data-api.binance.vision` (market-data-only хост, ключи не нужны) **делит счётчик
веса с `api.binance.com`** — проверено заголовками 24.08: 20 → 40 на data-api,
затем 60 на api. Отдельного бюджета он не даёт, на бан рассчитывать нельзя.

## 5. Прочие политики — где мы рядом с краем

### 5.1 Рыночные данные (ToS, раздел III.1.b.iv)

Без письменного согласия Binance запрещены:
- «Trading services that make use of Binance quotes or market bulletin board information.»
- «**Data feeding or streaming services** that make use of any market data of Binance.»
- «Any other websites/apps/services that **charge for or otherwise profit from**
  (including through advertising or referral fees) market data obtained from Binance.»

**Наш статус:** отчёты в Telegram содержат цены Binance и уходят подписчикам из
белого списка. Пока это личное, бесплатное и без рекламы — риск низкий.
**Красная линия:** как только за отчёты берут деньги, вешают рекламу или
реферальные ссылки — это прямой запрет. Не делать.

### 5.2 Один аккаунт — один человек

ToS: только одно физлицо или юрлицо на аккаунт, нельзя давать нескольким людям
доступ к одному аккаунту. Плюс запрет использовать сервис «for resale or
commercial purposes, including transactions on behalf of other persons or
entities, unless expressly agreed by Binance in writing».

**Наш статус:** мультиаккаунт сделан правильно — у каждого трейдера свой аккаунт
и свои ключи, состояние разведено по слотам. **Красная линия:** не заводить
чужие аккаунты на себя и не «торговать за человека» — ключ должен создавать сам
владелец у себя.

### 5.3 Безопасность ключей

Binance: «use IP whitelisting on all their API keys, regardless of the
permissions». И: ключ без белого списка IP, не использованный 30 дней, удаляется.

**Наш статус — самое слабое место:**
- **на боевом ключе включены выводы** (см. память проекта). Это самое опасное
  право, боту оно не нужно ни для чего. **Выключить.**
- белого списка IP нет. Railway даёт три статических адреса — их можно внести.
  Оговорка: адреса общие с другими клиентами Railway, так что белый список
  защищает от утечки ключа «наружу», но не от соседа по адресу.

### 5.4 MiCA / ЕС — следить

24.06.2026 Binance отозвала заявку на лицензию MiCA в Греции и заявила, что
будет получать её в другой стране ЕС ([блог](https://www.binance.com/en/blog/regulation/4457979419755346760)):
«Some users may be impacted depending on their country and account status».
Часть сторонних изданий пишет, что обслуживание клиентов из ЕС прекращено с
01.07.2026 — **у нас это не подтверждается**: аккаунт торгует, последний живой
цикл 24.08.2026 04:03 UTC. Держать как watch-item, а не как факт.

## 6. Чек-лист на будущее

- [x] 418/429 — отдельный тип ошибки, никаких ретраев внутрь бана
- [x] Ждать снятия бана внутри цикла, если укладываемся в `BAN_MAX_WAIT_MIN`
- [x] Прелёт-пинг весом 1 перед циклом
- [x] Читать и логировать `X-MBX-USED-WEIGHT-1M`
- [x] Один `exchangeInfo` на корзину
- [x] Падение цикла не роняет расписание Railway
- [ ] Выключить право на вывод у боевого ключа
- [ ] Внести три статических IP Railway в белый список ключа
- [ ] Никогда не монетизировать Telegram-отчёты с ценами Binance
- [ ] Каждый трейдер заводит ключ сам, на своём аккаунте
