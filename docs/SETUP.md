# Установка с помощью AI

Ниже — последовательность для Linux. Для другой ОС попросите AI адаптировать запуск. Сначала читайте PERSONALIZATION.md. Не запускайте reference-сборщик или sync до проверки источников.

## 1. Подготовка

Клонировать репозиторий в `~/agent-dashboard-starter`. Нужны Python 3.10+, Node для Firebase CLI/проверки JS, браузер, свои Claude Code и/или Codex CLI. Начать с одного провайдера, затем адаптировать маршрутизатор при необходимости. CLI должны быть авторизованы своим владельцем; подключённые инструменты приложения не обязательно доступны CLI.

Из корня репозитория:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp dashboard/public/firebase-config.example.js dashboard/public/firebase-config.js
```

Скопировать `deploy/environment.example` в `~/.config/agentdesk/environment`, заменить значения. Это systemd EnvironmentFile, а не исполняемый скрипт. Для запуска из терминала установите те же переменные через `export`. Без них используются значения из settings.py. Создать свою рабочую папку и `CONTEXT.md` с картой разрешённых источников и правилами работы.

Service account ожидается в `$AGENTDESK_BASE/config/serviceAccountKey.json`; Telegram — в `$AGENTDESK_BASE/config/kiwi_config.json`. Файлы хранить вне репозитория, права 600, родительские папки 700. Последний файл при использовании брокера имеет поля `bot_token` и `chat_id`. Реальные значения в Git и чат не добавлять.

## 2. Firebase

Создать собственный Firebase-проект. Включить Firestore и Google Sign-In, добавить домен Hosting в разрешённые домены Auth. Создать Web App и перенести его web config в `dashboard/public/firebase-config.js`. Web config будет виден браузеру по назначению; доступ защищают Auth и Rules, а не сокрытие web API key.

Создать свою учётную запись входом в приложение и взять UID из Firebase Console → Authentication. До этого правила с `REPLACE_WITH_OWNER_UID` должны оставаться закрытыми. Заменить UID в firestore.rules и storage.rules. Если нужно несколько человек, спроектировать разделение данных по владельцу: текущий шаблон рассчитан на одного владельца, не на SaaS.

Получить ключ сервисного аккаунта своего проекта и сохранить локально. Не использовать ключ автора. Подключить Storage только при необходимости вложений; требования сервиса и возможность платных ресурсов проверяются в своей консоли. Не активировать оплату автоматически.

С установленным Firebase CLI, из `dashboard/`:

```bash
firebase login
firebase use --add
firebase deploy --only firestore:rules,firestore:indexes,hosting
```

Storage rules развёртывать отдельно (`firebase deploy --only storage`), если Storage включён. Разрешения на развёртывание относятся к своему проекту. Локальный preview: `firebase serve --only hosting` либо статический HTTP-сервер; для Google Auth нужен разрешённый домен.

## 3. Проверки без реальных задач

```bash
cd agentdesk
../.venv/bin/python -m unittest discover -v
```

Тесты используют подмены CLI/хранилища и не доказывают доступ к Firebase или модели. Для app-тестов нужен локальный HTTP-порт. Регрессия test_fixes с историческим журналом ничего не проверяет без этого приватного файла; остальные проверки выполняются на синтетических данных.

Запустить локальное окно: `../.venv/bin/python app.py --no-window`, открыть выданный loopback URL. Модели и effort сверить с `app.MODELS`, `MANUAL_EFFORTS`, runtime.py и обоими frontend (`agentdesk/index.html`, `dashboard/public/app.js`). Удаление API-ключей из env сделано в clean_env; для API-режима потребуется отдельная продуманная адаптация.

## 4. Очередь

До старта worker адаптировать политики, проверить CLI на безопасном временном каталоге, настроить уведомления. `Store.notify()` зависит от внешнего `kiwi.notify`; заменить своим адаптером, возвращающим идентификатор доставки. Если оставить как есть, задачи могут выполниться, но уведомления будут иметь состояние uncertain.

Запуск вручную: `../.venv/bin/python board_worker.py` из agentdesk с заданными переменными. Задача в To do может реально изменять файлы. Сначала только синтетическая карточка. Worker использует администратора Firebase; правила браузера не ограничивают его.

## 5. Telegram и автозапуск

Настроить свой бот и chat_id. Для отдельного бота можно запустить telegram_broker.py без адаптации Claude Telegram. Не применять ensure_telegram_relay.py, если общий канал не нужен: он патчит установленный плагин.

Создать `~/.local/share/agentdesk`, скопировать service-файлы из deploy в `~/.config/systemd/user/`, проверить пути и окружение. После ручной приёмки:

```bash
systemctl --user daemon-reload
systemctl --user enable --now agentdesk-worker.service
systemctl --user enable --now agentdesk-telegram.service
```

Вторую службу включать только с настроенным Telegram. Проверить `systemctl --user status` и локальные журналы. Остановка: `systemctl --user stop agentdesk-worker.service agentdesk-telegram.service`. Пока компьютер выключен, локального исполнения нет. После остановки/перезапуска проверить состояние прерванных задач, не отправлять их в очередь вслепую.

## 6. Карта, проекты и расписания

Сначала своя схема источников, затем адаптация сборщика и sync. В `reference/agent-graph/build.py` путь и реестры — примеры. Скопировать адаптированный комплект в `$AGENTDESK_BASE/agent-graph`; sync ожидает build.py и graph.json именно там. В firebase_sync.py заменить BASE, каталоги источников, исключить все нежелательные выгрузки. Только после ручного запуска назначать расписание (в исходной системе — каждые 30 минут). Этот cron не должен исполнять карточки: это делает один worker.

Сохранять резервные копии Firestore и локального состояния, особенно approvals.sqlite3: потеря истории согласований требует ручной сверки, не автоматического повтора отправок.
