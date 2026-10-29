# Перевірка номерів Telegram

Застосунок перевіряє, чи є в Telegram акаунт на номері, через **ваш** користувацький акаунт (Telethon, MTProto). `api_id` і `api_hash` ви отримуєте на [my.telegram.org](https://my.telegram.org). Bot API номери не резолвить, сторонні lookup-сервіси не підключені.

Один і той самий HTTP API обслуговує інтерфейс і зовнішній застосунок.

## Запуск

Потрібні Docker і Docker Compose.

```bash
copy .env.example .env
```

Згенеруйте ключі й вставте їх у `.env`:

```bash
py -c "import secrets; print(secrets.token_urlsafe(24))"
py -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Перший рядок — `API_KEY`, другий — `SESSION_ENCRYPTION_KEY`. Пароль Postgres у `DATABASE_URL` має збігатися з `POSTGRES_PASSWORD`.

```bash
docker compose up --build -d
```

- Інтерфейс: http://localhost:8090
- API: http://localhost:8000
- Документація API: http://localhost:8000/docs

На першому екрані введіть `API_KEY`. Далі увійдіть кодом Telegram або вставте готову StringSession, запустіть перевірку і за потреби експортуйте CSV.

Сесії лежать у Postgres (volume `postgres_data`) у зашифрованому вигляді. API їх не повертає і не пише в логи. `docker compose down` дані зберігає. `docker compose down -v` видаляє volume разом із сесіями.

В одному запиті можна передати кілька акаунтів. Номери діляться між ними по колу, і кожен акаунт обробляє свою частку одночасно. Однаковий номер лишається на одному акаунті. Якщо Telegram повертає `FLOOD_WAIT`, пауза діє лише на цей акаунт; інші продовжують. Номери на інший акаунт не переносяться.

Одна StringSession не відкривається двома з’єднаннями одночасно, тож той самий акаунт у списку можна вказати лише раз.

Перевірка імпортує номери пакетами (типово 5), потім знімає їх через `contacts.deleteByPhones`. Для знайдених акаунтів додатково читається біо. `timeout` — пауза в секундах між запитами одного акаунта: між пакетами імпорту і між запитами біо. Кожен акаунт відлічує її окремо. Порожнє поле лишає стандарт: 5 с між пакетами і 1 с між запитами біо. Менше за 1 с між пакетами і 0,5 с між запитами біо пауза не ставиться.

Статус `not_found_or_private` означає одне: `PHONE_NOT_OCCUPIED` або порожній імпорт. Приватність може приховати наявний акаунт. Інші статуси рядка: `found`, `invalid_phone`, `retry_later`, `error`.

## API для зовнішнього застосунку

Усі запити, крім `GET /api/v1/health`, потребують заголовок `X-API-Key`.

Створити перевірку зі збереженої сесії:

```bash
curl -X POST http://localhost:8000/api/v1/checks ^
  -H "Content-Type: application/json" ^
  -H "X-API-Key: YOUR_API_KEY" ^
  -d "{\"phones\":[\"+380501112233\"],\"session_id\":\"SESSION_UUID\"}"
```

Або передати один чи кілька акаунтів у самому запиті. Сесія збережеться, але у відповіді її не буде. Замість `string_session` можна дати MTProto-ключ (`auth_key`, 256 байт у hex або base64) і `dc_id` від 1 до 5.

```json
{
  "phones": ["+380501112233", "0501234567"],
  "timeout": 4,
  "accounts": [
    {
      "api_id": 123456,
      "api_hash": "YOUR_API_HASH",
      "string_session": "YOUR_STRING_SESSION"
    },
    {
      "api_id": 123456,
      "api_hash": "YOUR_API_HASH",
      "auth_key": "MTPROTO_AUTH_KEY",
      "dc_id": 2
    }
  ],
  "callback_url": "https://example.com/hooks/tg-check"
}
```

Збережені сесії так само можна передати списком: `session_ids`.

Відповідь `202` — JSON:

```json
{"id": "...", "status": "queued", "total": 2, "processed": 0, "account_count": 2, "timeout": 4}
```

Результат: `GET /api/v1/checks/{id}` теж JSON. Поле `rows` містить номер, статус, id, ім’я, username, присутність (`online`, `offline` + `was_online`, `recently`, `last_week`, `last_month`, `hidden`), позначки акаунта і `bio`. `access_hash` не віддається.

Файл результату: `GET /api/v1/checks/{id}/export?format=json` або `?format=csv`. Без параметра відповідь — JSON. В інтерфейсі ті самі два формати є кнопками біля таблиці.

Якщо заданий `callback_url`, після завершення воркер надішле `POST` з підсумком і шляхом `result_path`. Повні рядки забирайте окремим `GET`.

Сесію можна зберегти заздалегідь: `POST /api/v1/auth/send-code`, потім `POST /api/v1/auth/sign-in`, або `POST /api/v1/sessions` з готовою StringSession.

## Локальні тести

Тести не ходять у Telegram.

```bash
cd backend
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```
