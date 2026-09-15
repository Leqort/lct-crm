# SPEC-01 — Ядро данных: модель, справочники, импорт XLSX, аудит

**Формат:** техническая спецификация для передачи ИИ-агенту (Claude Code / Cursor / аналог) на реализацию.
**Проект:** CRM ИТ Школы Ростелекома.
**Зона ответственности этой спеки:** Backend #1 — схема БД, CRUD справочников, импорт Excel-каталогов, аудит-лог.
**Вне этой спеки:** workflow-движок и переходы по этапам, генерация отчётов, интеграции с LMS/сайтом, фронтенд. Но модель данных должна их предусматривать — точки стыка описаны в §9.

---

## 1. Роль и контекст для агента

Ты — backend-разработчик на Python. Реализуешь фундамент CRM-системы, которая автоматизирует взаимодействие ИТ Школы Ростелекома с вузами. На этом фундаменте другие разработчики построят workflow-движок и отчёты, поэтому приоритет — корректность схемы, явные контракты и миграции, а не скорость.

**Правила работы:**
- Никаких заглушек `pass` и `TODO` в сдаваемом коде. Если что-то не реализуемо — явно скажи об этом в ответе, а не прячь в код.
- Все изменения схемы — только через Alembic-миграции. Ручной `create_all` в проде запрещён.
- Бизнес-логика живёт в сервисном слое, не в роутерах и не в моделях.
- Каждый публичный метод сервиса покрыт тестом.
- Сложные места (маппинг Excel, upsert-логика) сопровождаются комментариями — это требование заказчика.
- Код и комментарии — на английском, сообщения об ошибках для пользователя — на русском.

---

## 2. Стек

| Компонент | Версия/выбор |
|---|---|
| Python | 3.12 |
| Веб-фреймворк | FastAPI |
| ORM | SQLAlchemy 2.x (async, `asyncpg`) |
| Миграции | Alembic |
| Валидация | Pydantic v2 |
| БД | PostgreSQL 16 |
| Excel | `openpyxl` (xlsx) + `xlrd`/конвертация для legacy `.xls` |
| Тесты | pytest + pytest-asyncio + testcontainers (или отдельная тестовая БД) |
| Линт | ruff + mypy |

---

## 3. Структура проекта

```
app/
  main.py                 # сборка FastAPI, роутеры, middleware
  core/
    config.py             # Settings через pydantic-settings, всё из ENV
    db.py                 # async engine, session factory, get_session
    security.py           # заглушка current_user (см. §8)
    errors.py             # базовые исключения + коды ошибок
    audit.py              # контекст аудита (кто/откуда делает запрос)
  models/                 # SQLAlchemy ORM-модели
  schemas/                # Pydantic-схемы запросов/ответов
  repositories/           # доступ к данным, без бизнес-логики
  services/               # бизнес-логика: catalogs, import, audit
  api/v1/                 # роутеры
  imports/
    parser.py             # чтение xls/xlsx в сырые строки
    mapping.py            # пресеты маппинга колонок
    validators.py         # правила валидации строк
    importer.py           # dry-run и commit
migrations/               # Alembic
tests/
docker-compose.yml
Dockerfile
```

---

## 4. Модель данных

### 4.1 Общие правила для всех таблиц

- PK — `UUID` (`uuid4`), колонка `id`.
- У каждой доменной таблицы: `created_at`, `updated_at` (timestamptz, UTC, server_default now()), `created_by`, `updated_by` (UUID пользователя, nullable).
- **Удаление — только мягкое**: колонка `deleted_at timestamptz NULL`. Жёсткий DELETE запрещён: это требование аудита и 152-ФЗ. Все выборки по умолчанию фильтруют `deleted_at IS NULL`.
- Все строковые enum'ы — через PostgreSQL enum или отдельный справочник, не свободный текст.
- Названия таблиц — snake_case, множественное число.

### 4.2 Справочники

**`universities` — вузы**

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| name | text NOT NULL | Уникально в рамках `deleted_at IS NULL` |
| short_name | text NULL | |
| region | text NULL | |
| inn | text NULL | Для будущего матчинга по Excel |
| external_id | text NULL | ID из LMS/сайта, уникален при наличии |
| comment | text NULL | |

**`vendors` — вендоры ПО**

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| name | text NOT NULL UNIQUE | |

**`it_directions` — ИТ-направления** (DevOps, QA, Data Science...)

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| name | text NOT NULL UNIQUE | |
| description | text NULL | |
| is_active | bool NOT NULL DEFAULT true | |

**`it_products` — ИТ-продукты (ПО/лицензии)**

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| name | text NOT NULL | Уникально в паре с `vendor_id` |
| vendor_id | UUID FK → vendors | NULL допустим |
| description | text NULL | |
| is_active | bool NOT NULL DEFAULT true | |

**`it_products_directions`** — связь many-to-many продукта и направлений (PK составной).

**`users` — сотрудники Школы**
Мастер-источник — Keycloak. Локально храним проекцию для связей и отображения ФИО.

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| keycloak_id | text NOT NULL UNIQUE | `sub` из токена |
| full_name | text NOT NULL | |
| email | text NULL | |
| role | enum(`user`,`manager`,`admin`) NOT NULL | Кэш роли из токена, источник истины — токен |
| is_active | bool NOT NULL DEFAULT true | |

**`university_contacts` — ответственные со стороны вуза**
Это персональные данные → минимизация: только ФИО, должность, рабочий контакт.

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| university_id | UUID FK → universities NOT NULL | |
| full_name | text NOT NULL | |
| position | text NULL | |
| email | text NULL | |
| phone | text NULL | |
| is_primary | bool NOT NULL DEFAULT false | |

**`university_assignments` — закрепление КАМа за вузом**
Определяет видимость данных для роли «Пользователь».

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| university_id | UUID FK NOT NULL | |
| user_id | UUID FK → users NOT NULL | |
| assigned_from | date NOT NULL | |
| assigned_to | date NULL | NULL = действует сейчас |

Ограничение: у вуза не может быть двух активных назначений с пересекающимися периодами — проверять в сервисе и, если получится, exclusion constraint.

### 4.3 Основная сущность

**`interactions` — взаимодействие**
Одна карточка = вуз + ИТ-направление + ИТ-продукт. Именно она поедет по workflow (это делает другой разработчик, но таблицу и поля из Excel заводишь ты).

| Поле | Тип | Правила |
|---|---|---|
| id | UUID PK | |
| university_id | UUID FK NOT NULL | |
| it_direction_id | UUID FK NULL | |
| it_product_id | UUID FK NULL | |
| responsible_user_id | UUID FK → users NULL | «ФИО Менеджера» из Excel |
| contract_number | text NULL | «Номер договора» |
| license_signed_at | date NULL | «Подписание лицензии» |
| license_years | smallint NULL | «Срок действия лицензии (год)» |
| license_expires_at | date NULL | Вычисляется из двух полей выше, если оба заданы |
| transfer_status | text NULL | «Статус по передаче» — сырое значение из Excel |
| comment | text NULL | |
| workflow_version_id | UUID NULL | **Заглушка под SPEC-02.** FK добавит соседняя команда |
| current_stage_id | UUID NULL | **Заглушка под SPEC-02** |

Уникальность: `(university_id, it_direction_id, it_product_id)` при `deleted_at IS NULL` — партиальный уникальный индекс. Это ключ для upsert при импорте.

Индексы: по `university_id`, `responsible_user_id`, `license_expires_at`, составной по `(university_id, it_product_id)`.

### 4.4 Импорт

**`import_jobs`**

| Поле | Тип |
|---|---|
| id | UUID PK |
| filename | text NOT NULL |
| file_hash | text NOT NULL — sha256 содержимого |
| target | enum(`universities`,`it_products`,`interactions`,`contacts`) |
| status | enum(`pending`,`validated`,`committed`,`failed`,`cancelled`) |
| mapping | jsonb — применённый маппинг колонок |
| stats | jsonb — `{total, to_create, to_update, skipped, errors}` |
| created_by | UUID |
| created_at / committed_at | timestamptz |

**`import_rows`** — построчный результат разбора: `job_id`, `row_number`, `raw_data jsonb`, `status` (`ok`/`warning`/`error`), `messages jsonb`, `resolved_entity_id UUID NULL`. Нужна, чтобы показать пользователю предпросмотр и отчёт об ошибках.

**`import_mapping_presets`** — сохранённые пресеты: `name`, `target`, `mapping jsonb`, `created_by`. Чтобы менеджер не размечал колонки заново каждый месяц.

### 4.5 Аудит

**`audit_log`** — append-only, апдейты и удаления запрещены на уровне прав БД.

| Поле | Тип | Комментарий |
|---|---|---|
| id | bigserial PK | |
| occurred_at | timestamptz NOT NULL DEFAULT now() | |
| actor_id | UUID NULL | NULL = системное действие |
| actor_name | text NULL | Денормализовано: пользователя могут удалить, лог остаётся |
| action | enum(`create`,`update`,`delete`,`read_pd`,`import`,`export`,`login`,`access_denied`) | |
| entity_type | text NOT NULL | |
| entity_id | UUID NULL | |
| changes | jsonb NULL | `{field: {old, new}}`, только изменённые поля |
| ip_address | inet NULL | |
| user_agent | text NULL | |
| request_id | uuid NULL | Сквозной ID запроса |

Партиционирование по месяцу — желательно, но не блокирующее. Индексы: `(entity_type, entity_id)`, `(actor_id, occurred_at)`, `occurred_at`.

---

## 5. Требования к аудиту

1. Пишется автоматически, разработчик не должен помнить про вызов вручную. Реализация — SQLAlchemy-события (`after_insert`, `after_update`, `after_delete`) + контекст запроса через `contextvars`.
2. В `changes` попадают **только реально изменившиеся поля**, `created_at`/`updated_at` исключаются.
3. Значения полей, помеченных как ПДн (`full_name`, `email`, `phone`), в `changes` **маскируются**: сохраняем факт изменения и хеш, но не оба открытых значения. Открытые значения остаются в самой таблице сущности.
4. Отдельно логируются: неудачные попытки доступа (`access_denied`), выгрузка данных (`export`), импорт (`import`).
5. Запись аудита не должна ронять основную операцию: ошибка логирования пишется в application log, транзакция не откатывается.
6. Аудит-лог доступен только роли `admin`, эндпоинт — только чтение с фильтрами.

---

## 6. Импорт XLSX — детальный сценарий

Процесс двухфазный: сначала **dry-run**, потом **commit**. Пользователь обязан увидеть предпросмотр до записи в БД.

**Шаг 1. Загрузка.** `POST /api/v1/imports` с файлом. Проверки:
- расширение `.xls` / `.xlsx`, реальный MIME по сигнатуре файла (не доверять имени);
- размер ≤ 20 МБ;
- файл читается, есть хотя бы одна непустая строка данных.
Ответ: `job_id`, список заголовков из первой строки, автоматически предложенный маппинг (fuzzy-сопоставление с ожидаемыми полями).

**Шаг 2. Маппинг.** `POST /api/v1/imports/{job_id}/mapping` — пользователь подтверждает или правит соответствие «колонка файла → поле системы». Можно сохранить как пресет.

Ожидаемые поля каталога заказчика (названия колонок менять нельзя, они приходят как есть):

| Колонка Excel | Целевое поле |
|---|---|
| Название ВУЗа | `universities.name` (find-or-create) |
| Вендор | `vendors.name` (find-or-create) |
| ПО | `it_products.name` (find-or-create в паре с вендором) |
| Номер договора | `interactions.contract_number` |
| Подписание лицензии | `interactions.license_signed_at` |
| Срок действия лицензии (год) | `interactions.license_years` |
| Статус по передаче | `interactions.transfer_status` |
| ФИО Менеджера | `interactions.responsible_user_id` (матч по ФИО в `users`) |
| Ответственные от ВУЗа | `university_contacts.full_name` (может быть несколько через `;` или `,`) |
| Комментарий | `interactions.comment` |

**Шаг 3. Валидация (dry-run).** `POST /api/v1/imports/{job_id}/validate`. Для каждой строки:
- нормализация: обрезка пробелов, схлопывание двойных пробелов, приведение регистра названий вузов для сравнения (но в БД пишем как в файле);
- даты: поддержать и excel-serial, и строки `ДД.ММ.ГГГГ`, `ГГГГ-ММ-ДД`;
- числа: срок лицензии — целое 1..10, иначе `warning` и поле пустое;
- матчинг вуза: точное совпадение по нормализованному имени → иначе поиск похожего (Levenshtein/trigram) → если похожий найден с высокой уверенностью, помечаем строку `warning` с предложением, но **не сливаем автоматически**;
- матчинг менеджера по ФИО: не нашли — `warning`, поле остаётся пустым, строка импортируется;
- пустое «Название ВУЗа» → `error`, строка не импортируется;
- дубли внутри файла → `warning`, применяется последняя строка.

Результат: `import_rows` заполнена, `stats` посчитана, статус `validated`. Ответ отдаёт постранично строки с их статусами и сообщениями.

**Шаг 4. Commit.** `POST /api/v1/imports/{job_id}/commit`.
- Всё в **одной транзакции**. Любая непредвиденная ошибка → полный откат, статус `failed`.
- Строки со статусом `error` пропускаются, не блокируя остальные.
- Upsert по бизнес-ключу `(university, direction, product)`: существующая запись **обновляется**, новая создаётся. Дубли не плодятся — это жёсткое требование.
- При обновлении: пустая ячейка в файле **не затирает** заполненное значение в БД. Пустое = «нет данных», а не «очистить».
- Пишется одна запись `audit_log` с `action=import` и сводной статистикой + обычные `create`/`update` по затронутым сущностям.
- Повторная загрузка того же файла (совпал `file_hash`) → предупреждение, но не блокировка; результат идемпотентен благодаря upsert.

**Шаг 5. Отчёт.** `GET /api/v1/imports/{job_id}/report` — выгрузка xlsx с исходными строками и колонкой результата, чтобы пользователь исправил файл и загрузил повторно.

---

## 7. API

Все пути с префиксом `/api/v1`. Все списочные эндпоинты: пагинация (`page`, `size`, максимум 200), сортировка (`sort=field,-field`), фильтр `search` по названию.

**Справочники** (единый паттерн для `universities`, `vendors`, `it-directions`, `it-products`, `university-contacts`):

| Метод | Путь | Роль |
|---|---|---|
| GET | `/universities` | любая (с фильтрацией по доступу) |
| GET | `/universities/{id}` | любая |
| POST | `/universities` | manager, admin |
| PATCH | `/universities/{id}` | manager, admin |
| DELETE | `/universities/{id}` | admin (мягкое удаление) |

**Назначения:**
- `GET /universities/{id}/assignments`
- `POST /universities/{id}/assignments` — роль manager/admin
- `DELETE /assignments/{id}` — закрывает период, не удаляет запись

**Импорт:** `POST /imports`, `POST /imports/{id}/mapping`, `POST /imports/{id}/validate`, `GET /imports/{id}/rows`, `POST /imports/{id}/commit`, `GET /imports/{id}/report`, `GET /imports` (история), `GET/POST /imports/presets`.

**Аудит:** `GET /audit` — только admin. Фильтры: `actor_id`, `entity_type`, `entity_id`, `action`, `date_from`, `date_to`.

**Swagger UI обязателен** — каждый эндпоинт с `summary`, `description`, примерами ответов и описанием кодов ошибок. Это отдельный пункт приёмки у заказчика.

---

## 8. Права доступа и заглушка авторизации

Keycloak подключает другой разработчик. Ты делаешь абстракцию и временную заглушку:

```python
# core/security.py
class CurrentUser(BaseModel):
    id: UUID
    keycloak_id: str
    full_name: str
    role: Literal["user", "manager", "admin"]

async def get_current_user(...) -> CurrentUser: ...
```

В dev-режиме (`AUTH_MODE=dev`) пользователь берётся из заголовка `X-Debug-User`. В проде (`AUTH_MODE=keycloak`) — из JWT. Переключение только через ENV, dev-режим должен быть невозможен при `ENV=production` (падать на старте).

**Правило видимости, обязательное к реализации на уровне репозитория, а не роутера:**
- `user` — видит только вузы, где он активный ответственный в `university_assignments`;
- `manager` и `admin` — видят всё.

Фильтр применяется в базовом репозитории, чтобы его нельзя было случайно забыть. Попытка запросить чужой объект по прямому ID → `403` и запись `access_denied` в аудит (не `404`, чтобы не скрывать факт отказа от аудита).

---

## 9. Точки стыка с другими спеками

Ты **не реализуешь**, но обязан предусмотреть:
- `interactions.workflow_version_id` и `current_stage_id` — nullable, FK добавит SPEC-02 отдельной миграцией. Не завязывай на них логику.
- Отчёты (SPEC-03) будут читать `interactions` с джойнами по всем справочникам — обеспечь индексы под фильтрацию по периоду, вузу, направлению, продукту, ответственному.
- Интеграции (SPEC-04) будут писать в те же справочники — поэтому вся логика find-or-create и нормализации названий должна лежать в сервисном слое и вызываться извне, а не быть зашита в парсер Excel.

---

## 10. Обработка ошибок

Единый формат ответа:

```json
{
  "error": {
    "code": "IMPORT_INVALID_FORMAT",
    "message": "Файл не является корректной таблицей Excel",
    "details": {"filename": "catalog.xlsx"},
    "request_id": "0d2f..."
  }
}
```

Минимальный набор кодов: `VALIDATION_ERROR`, `NOT_FOUND`, `ACCESS_DENIED`, `DUPLICATE_ENTITY`, `IMPORT_INVALID_FORMAT`, `IMPORT_FILE_TOO_LARGE`, `IMPORT_MAPPING_INCOMPLETE`, `IMPORT_JOB_WRONG_STATE`, `INTERNAL_ERROR`. Наличие внятных кодов ошибок — прямое требование ТЗ (НФТ №3).

---

## 11. Тесты

Обязательный минимум:
- upsert при импорте: повторная загрузка того же файла не создаёт дублей;
- пустая ячейка не затирает заполненное поле в БД;
- строка с ошибкой пропускается, остальные импортируются;
- разбор дат во всех трёх форматах;
- вуз с похожим названием не сливается автоматически, а помечается warning;
- `user` не видит чужой вуз ни в списке, ни по прямому ID;
- аудит фиксирует update с корректным diff и маскирует ПДн;
- мягкое удаление исключает объект из выдачи, но запись остаётся в БД;
- транзакционность commit: искусственная ошибка в середине → ни одной записи не добавилось.

---

## 12. Definition of Done

- [ ] `docker compose up` поднимает postgres + приложение, миграции применяются автоматически
- [ ] Alembic: `upgrade head` и `downgrade base` отрабатывают без ошибок
- [ ] Сид-скрипт создаёт тестовых пользователей всех трёх ролей и демо-справочники
- [ ] Swagger UI на `/docs`, все эндпоинты описаны
- [ ] Импорт реального файла заказчика проходит полный цикл: загрузка → маппинг → предпросмотр → commit
- [ ] Повторный импорт того же файла не создаёт дублей
- [ ] Аудит пишется на все изменения, доступен админу через API
- [ ] `ruff check` и `mypy` без ошибок
- [ ] Тесты из §11 зелёные
- [ ] README: запуск, переменные окружения, описание модели данных, перечень библиотек

---

## 13. Что делать при неоднозначности

Если в спеке чего-то не хватает — не выдумывай тихо. Выбери самый консервативный вариант, реализуй его и **вынеси список принятых допущений отдельным разделом в ответе и в README**. Отдельно отметь всё, что касается персональных данных: там решение принимает заказчик, а не разработчик.
