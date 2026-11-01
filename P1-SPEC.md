# ЗАДАНИЕ: P1-фиксы фабрикиkanban (lease / evidence-gate / real-adapter gate / вердикт-валидатор)

Контекст: репозиторий — ядро Hermes Agent (kanban-фабрика). Ты в ветке wt/factory-upgrade-p1. Живой runtime работает из ДРУГОЙ копии этого репо — здесь только код+тесты+коммиты. ПУШИ ЗАПРЕЩЕНЫ. Прод-данные не трогать.

Merge-цель: ветка деплой-копии hermes-agent, отпочкованная от
`a42450390cb5e16ae299390796ab669cbc73df47`. Это НЕ upstream main и НЕ fork main;
в эту ветку нельзя мержить расходящуюся историю fork main с десятками тысяч чужих коммитов.

Изучи перед началом: hermes_cli/kanban_db.py (claim/complete_task/lease), hermes_cli/kanban_db_dispatch.py (dispatch/spawn), tests/ (pytest). Флаг completion-contract существует в `hermes kanban create --completion-contract CONTRACT` — найди его plumbing (grep completion_contract в hermes_cli/).

## WP1 — Lease на карточку с run_id (защита от двойного исполнителя)

Инцидент, который надо закрыть: две сессии работали по одной карточке параллельно (30.10, B1). 
Задачи:
1. Проследи путь: claim (CAS) → run_id → heartbeat/reclaim. Найди ЛЮБОЙ путь, по которому второй воркер/второй dispatch может начать работу по карточке с живым claim (claim_expires в будущем): обход CAS, повторный dispatch, complete-конфликт.
2. Если дыра есть — закрой fail-closed: второй спавн по карте с живым claim должен ОТКАЗАТЬСЯ (не молча переиспользовать).
3. Негативный тест в tests/: два claim/spawn на одну карту → второй получает отказ/ошибку. Тест обязан падать на старом коде (сначала запусти на старом — задокументируй результат, потом фикс).

## WP2 — Evidence-gate на закрытие (done = доказательство)

Факт аудита: worker-initiated close ушёл в done с пустым result (API PATCH уже блокирует done-без-result; закрытие воркером обошло). 
Задачи:
1. Найди путь закрытия карточки воркером (kanban_db complete_task / CLI complete) и сделай инвариант единым: ЛЮБОЙ переход в done требует непустого result (или явного override-флага для админ-закрытий). Путь через API и через CLI — одинаковое правило.
2. Real-adapter gate: если completion-contract карточки содержит require_real_adapter: true — переход в done ДОПУСТИМ только если result содержит строку-маркер `real-adapter: <suite> <N passed>` (регэксп, >=1 passed, без обрезки по краям). Иначе отказ с внятной ошибкой; инструментальный путь ловит и RealAdapterEvidenceError.
3. Тесты: (а) close без result → отказ (worker-путь); (б) contract require_real_adapter + result без маркера → отказ; (в) с маркером → ок.

### Fix-round решение по review-аппрувам (вердикт 2026-10-31, blocker-2; уточнено владельцем 01.11 — вариант б)
Exemption ограничен: завершение карточки из статуса `review` освобождает ТОЛЬКО от
правила непустого `result` (человеческое одобрение — само evidence; durably пишется
`_REVIEW_APPROVED_NOTE` ("Review approved without additional evidence.") на run и
событие completed, аудируемо). Маркер real-adapter ОБЯЗАТЕЛЕН для любого перехода
в done, включая закрытие из `review`: карточка с `require_real_adapter` не может
быть одобрена из review без строки-маркера в result. Для всех остальных
source-status инвариант строгий: пустой/пробельный result → EmptyCompletionError.
Тесты: review-approval без result → done с нотой; review-approval с result → в
result записан переданный текст; review-закрытие require_real_adapter без маркера →
отказ (RealAdapterEvidenceError), с маркером → done; пустой close вне review → отказ.

## WP3 — Валидатор вердиктов (неизменяемая запись с head SHA)

Канон вердиктов: файл в отдельном repo fleet-reviews. Поддерживаются оба формата:
(1) фактический канон fleet-reviews v1.3 — JSON frontmatter между `---`-фенсами
(status ok|issues|incomplete, reviewed_range{base,head 40-hex}, spec_hash,
evidence, findings[], notes[], escalations[]; карточка = имя каталога
reviews/<owner-repo>/, review_id = имя файла без расширения, если не задан явно);
(2) карточный формат фабрики — первая строка `STATUS: PASS|REQUEST-CHANGES|REVOKE`,
далее review_id, card, base_sha, head_sha (40-hex), evidence-блок. Новая
информация: `supersedes: <review_id>` отзывает старый вердикт той же карты+head;
самоссылка (A→A), взаимный цикл (A↔B) и неизвестные ключи (в т.ч. опечатка
`supersede:`) — ошибка выхода 1.
Задачи:
1. Скрипт scripts/validate_verdict.py (argparse, stdlib): проверяет md-файл(ы) — обязательные поля, формат SHA, уникальность review_id, при двух активных вердиктах на одну карту+head без supersedes у нового → ошибка выхода 1.
2. Юнит-тест: валидный вердикт ок; без head_sha → ошибка; дубль без supersedes → ошибка; дубль с supersedes → ок.

## Правила исполнения
- Коммиты: только явные пути (git add <file>), НЕ `git add -A`. Коммиты по одному на WP, сообщения: `p1(wp1): ...` / `p1(wp2): ...` / `p1(wp3): ...`.
- НЕ менять: plugins/kanban/dashboard/dist/*, config.yaml, .env, любые секреты.
- Каждый WP завершай прогоном новых тестов (pytest -x <файл>) и вставь вывод в отчёт.
- Финал: `git log --oneline origin..HEAD` + `git diff --stat origin..HEAD` + список прогнанных тестов с числом passed.
- Не выдумывай API: если функция не найдена — grep/читай код, не предполагай.
