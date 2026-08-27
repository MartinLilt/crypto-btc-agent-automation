# Свой egress IP для Binance — установка и переключение

Составлено 2026-08-25 после **третьего** бана подряд (23.08, 24.08, 25.08).

## Зачем

Binance считает вес и банит **по IP, а не по ключу**: «all connections from a
banned IP address are blocked, affecting all accounts using that IP». Наш цикл
тратит ~38 единиц веса раз в 4 часа при лимите 6000 **в минуту** — 0.6% бюджета
одной минуты. Все три бана уже действовали на первом же нашем запросе.

Причина — в [доках Railway](https://docs.railway.com/networking/static-outbound-ips):

> «There is no guarantee that the IPv4 addresses assigned to your service are
> dedicated. **They may be shared with other customers.**»

Настройки «дать выделенный» у Railway нет ни в UI, ни в CLI. Обход через
`data-api.binance.vision` проверен 24.08 и **не работает** — общий счётчик веса.
Значит лечится только снаружи: свой адрес, на котором кроме нас никого.

### Проверено 25.08 в живом интерфейсе, а не только по докам

`grid-stream → Settings → Networking → Static Outbound IPs`: три адреса, у
каждого в колонке **Type** стоит **Shared**, подсказка — «This IP may be shared
with other customers». Это Railway про наши собственные адреса.

Наши три (нужны для файрвола на шаге 3):

```
208.77.244.242
152.55.184.240
152.55.184.241
```

В полной таблице тарифов на railway.com/pricing раздел **Networking** (TCP
proxy, HTTP proxy, DDoS, приватная сеть, IPv6) **не содержит строки про
выделенный IP ни на одном тарифе, включая Enterprise**. Единственное
«выделенное» у них — **Dedicated VMs**, и по их же таблице разблокировки это
обязательство **$10 000/мес**. Вопрос закрыт: внутри Railway решения нет ни за
какие разумные деньги.

Там же подтвердилось, что **Hobby сохраняет «Global regions» и 50 cron-задач** —
при понижении тарифа Amsterdam и расписание остаются, теряются ровно статические
IP, которые после переезда не нужны.

Итог по деньгам: Pro ($20) нужен нам **только** ради Static Outbound IPs. После
переезда они не нужны → Hobby ($5) + коробка (~€4.4) ≈ **$9.8 против $20**.
Потребление проверено 25.08: Postgres держит 0.038 GB RAM и 0.0003 vCPU ≈
$0.4/мес, во включённые в Hobby $5 влезаем с запасом.

---

## Порядок действий

Важен именно такой порядок: на каждом шаге живой бот продолжает торговать, а
откат — это снятие одной переменной.

### 1. Коробка

Hetzner Cloud, **CX22** (2 vCPU / 4 GB) ≈ €3.79/мес + выделенный IPv4 €0.60/мес.
Локация — **Falkenstein / Nuremberg (DE)** или **Helsinki (FI)**. Образ Ubuntu 24.04.
Хватило бы и меньшего, но CX22 — младший тариф с нормальным IPv4.

При создании включить **Public IPv4** (без него нет смысла) и залить SSH-ключ.

### 2. Проверить, что Binance с этого адреса вообще отвечает

**До** всякой настройки, иначе можно купить бесполезный адрес:

```bash
ssh root@<vps-ip>
curl -s -o /dev/null -w '%{http_code}\n' https://api.binance.com/api/v3/ping
curl -sI https://api.binance.com/api/v3/time | grep -i x-mbx-used-weight
```

Ждём `200` и заголовок с **маленьким** весом (единицы, не тысячи). Если прилетел
`451` — гео-блок, локация не годится, брать другую. Если вес сразу большой —
адрес не выделенный, писать в поддержку и менять.

### 3. Прокси

```bash
apt update && apt install -y tinyproxy ufw
```

`/etc/tinyproxy/tinyproxy.conf` — заменить содержимое на:

```
User tinyproxy
Group tinyproxy
Port 8443
Timeout 60
LogLevel Warning
MaxClients 20

# Кто может подключаться. Это дубль к ufw — второй замок на той же двери.
Allow <railway-ip-1>
Allow <railway-ip-2>
Allow <railway-ip-3>

BasicAuth bot <длинный-случайный-пароль>

# CONNECT только на 443. Без этой строки прокси станет открытым релеем
# для любого порта, и адрес забанят уже за дело.
ConnectPort 443

Filter "/etc/tinyproxy/binance.txt"
FilterExtended On
FilterDefaultDeny Yes
```

`/etc/tinyproxy/binance.txt` — куда вообще можно ходить:

```
^api\.binance\.com$
^fapi\.binance\.com$
^testnet\.binance\.vision$
```

Три адреса Railway — те, что выписаны выше (перепроверить в **Settings →
Networking → Static IPs**, если сервис передеплоят в другой регион). Пароль —
`openssl rand -base64 24`.

Файрвол:

```bash
ufw default deny incoming
ufw allow 22/tcp
ufw allow from <railway-ip-1> to any port 8443 proto tcp
ufw allow from <railway-ip-2> to any port 8443 proto tcp
ufw allow from <railway-ip-3> to any port 8443 proto tcp
ufw --force enable

systemctl enable --now tinyproxy
systemctl restart tinyproxy
apt install -y unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades
```

Проверка **с самой коробки** (должно быть 200 — локалхост в `Allow` не входит,
поэтому проверяем через ufw-разрешённый путь или временно добавив `Allow 127.0.0.1`):

```bash
curl -x http://bot:<пароль>@127.0.0.1:8443 -s -o /dev/null -w '%{http_code}\n' \
     https://api.binance.com/api/v3/ping
```

### Про безопасность пароля в открытом виде

Пароль к прокси уходит по HTTP в открытую. Это **осознанно и не страшно**: ключи
Binance летят внутри CONNECT-туннеля, TLS до `api.binance.com` не расшифровывается
прокси никогда, и прокси их не видит. Утечь может только пароль от прокси — а
порт закрыт файрволом для всех, кроме трёх адресов Railway. Хочется третий замок —
поднять tinyproxy за stunnel/Caddy с TLS, но это уже излишество.

### 4. Binance: белый список ключа

В аккаунте Binance → API Management → у ключа **Edit restrictions**:

- добавить `<vps-ip>`,
- **три адреса Railway пока НЕ убирать** — это путь отката,
- заодно закрыть долг из чек-листа: **Enable Withdrawals — OFF**.

### 5. Railway: включить

Переменные сервиса `grid-stream`:

```
BINANCE_PROXY_URL=http://bot:<пароль>@<vps-ip>:8443
BINANCE_PROXY_FALLBACK=true
BAN_MAX_WAIT_MIN=180
```

Redeploy. Дождаться цикла и посмотреть логи — должно быть:

```
IP weight (1m)    : 38/6000 peak
Egress            : own dedicated IP ✅
```

**`Egress: own dedicated IP ✅` + пик веса в районе 38 — это и есть
доказательство.** На чужом адресе пик был 5890. Если пик высокий, а строка
говорит «own» — трафик идёт мимо прокси либо IP не выделенный, бот проорёт об
этом сам (см. `weight_report()` в `src/ratelimit.py`).

### 6. Через 2–3 чистых суток — закрепить

Только после того, как несколько циклов подряд прошли чисто:

1. Binance: убрать три адреса Railway из белого списка, оставить один `<vps-ip>`.
2. Railway: `BINANCE_PROXY_FALLBACK=false` — падать громко, а не тихо выходить
   с общего адреса и ловить `-2015`.
3. Railway → Settings → Networking → **выключить Static IPs**.
4. Railway → аккаунт → тариф **Pro → Hobby**.

Шаги 3 и 4 — необратимая часть: обратно те же три адреса не выдадут. Поэтому
только после доказательства из шага 5.

---

## Откат

На любом этапе до шага 6: снять `BINANCE_PROXY_URL` в Railway и передеплоить.
Бот мгновенно возвращается к прежнему поведению — код с пустой переменной ведёт
себя байт в байт как до правки (пришпилено тестом
`test_no_proxy_configured_is_exactly_the_old_direct_behaviour`).

## Что осталось общим и после переезда

Egress лечит **бан по IP**. Ордерные лимиты (100 за 10 сек, 200k в сутки)
считаются по аккаунту и переездом не меняются — но мы их и близко не трогаем.

## Связанное

- [[Binance API policy]] — лимиты, веса, ToS, чек-лист
- `src/net.py` — единственная точка выхода наружу, там же вся аргументация
- `tests/test_net.py` — почему read-timeout **никогда** не переигрывается

---

## Что из этого осталось после 27.08.2026

Шаги 3 и 5 (tinyproxy, `BINANCE_PROXY_URL`) **не понадобились**: вместо гибрида
«Railway + прокси на коробке» переехали целиком. Бот живёт на самой коробке,
хопа нет, падать нечему. Прокси-код в `src/net.py` остаётся рабочим и инертным —
он включается одной переменной, если когда-нибудь снова понадобится гибрид.

Актуальная коробка: **cx23, Nuremberg, `46.225.162.161`**, Ubuntu 24.04,
пользователь `bot`, ключ `~/.ssh/binance_egress`, юниты
`grid-stream.timer` (цикл раз в 4ч) и `pg-backup.timer` (ночной дамп).
Конфиг — `/etc/grid-stream.env` (root:bot, 0640), база — локальный Postgres 18.
Исходники и юниты — в репозитории, `deploy/hetzner/`.

Строка-доказательство в отчёте теперь другая: **`Egress: own dedicated IP ✅
(this host)`** плюс вес в районе десятка. Даёт её `EGRESS_DEDICATED=true`.

Проверено при заезде: `ping` 200, `x-mbx-used-weight-1m: 2`, гео-блока нет.

**Про наличие у Hetzner:** поле `available` в их же API врёт (см. Dev Log за
27.08) — единственная честная проверка это попытка создать сервер.
