# Восстановление из бэкапа

## Где лежат копии

1. **На сервере:** `/var/backups/grid/grid-YYYY-MM-DDTHH-MM.sql.gz`, последние 14 ночей.
   Делаются каждую ночь в 03:17 UTC.
2. **Вне сервера:** тот же дамп, зашифрованный `age`. Приходит в Telegram-чат владельца
   без звука, файлом `grid-….sql.gz.age`.
   - Публичный ключ лежит на сервере в `/etc/grid-stream.env` (`BACKUP_AGE_RECIPIENT`).
   - **Приватный ключ есть только у владельца:** `~/.config/grid-stream/backup.agekey` на Mac
     и копия в менеджере паролей. Без него файлы в Telegram не прочитать никому, в том числе нам.

## Проверка (учебное восстановление)

Нужны `age` и Docker (Docker Desktop должен быть запущен).

```bash
# файл, скачанный из Telegram
tools/restore_check.sh ~/Downloads/grid-2026-11-23T03-17.sql.gz.age
# или по file_id из журнала: journalctl -u pg-backup.service | grep file_id
tools/restore_check.sh --file-id <file_id>
```

Скрипт расшифрует файл, поднимет временный Postgres 18 и восстановит туда дамп. Потом
выведет число сделок, `max(id)`, сумму P&L, `cash` и сумму пополнений. Эти цифры нужно сравнить
с боевой базой:

```bash
sudo -u postgres psql -d grid -At -c "select count(*), max(id), sum(pnl) from trades"
```

Первая проверка, 2026-11-22: файл из Telegram → Mac → Postgres 18 совпал с боевой базой
до последней цифры (96 сделок, max id 100, P&L 13.7694, cash 79.62161788, flows 126.7206).

## Настоящее восстановление (сервер потерян)

1. Новый сервер: `deploy/server/cloud-init.yaml`, затем `sudo bash deploy/server/setup.sh`.
2. Расшифровать последний дамп на Mac:
   `age -d -i ~/.config/grid-stream/backup.agekey -o grid.sql.gz grid-….sql.gz.age`.
3. Скопировать на сервер и восстановить: `gunzip -c grid.sql.gz | sudo -u postgres psql -d grid`.
   Таблицы должны принадлежать `grid` (`ALTER TABLE … OWNER TO grid`, если нужно).
4. **Сверить учёт с реальными балансами Binance**, как это сделано 2026-10-09.
   Сделки между дампом и падением сервера в дампе отсутствуют.
5. Сначала бумажный цикл на клоне базы, потом включить таймер (это делает владелец).
6. Новый IP → новый API-ключ Binance с белым списком IP, обновить `private/ops.md`.
