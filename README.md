# HW7 · Observability & Cost Report

Домашнє завдання для PayPilot. Замість L03 використано адаптований ранер
із HW4, оскільки локальний L03 не підтримував assertions набору та потрібний
підрахунок токенів діалогу.

- [Заповнений звіт](./l08/observability-cost-report.md) і
  [порожній шаблон](./l08/observability-cost-report.template.md).
- [Діалогові кейси з бюджетами](./l05_dialogs.jsonl) — у корені, як очікує
  ранер HW4; папка `sets/` не використовується.
- [Журнал виконання](./HW7.md), [модель вартості](./l08/cost-model.md),
  [B4](./l08/b4.json) і [результати прогонів](./reports/).

## Запуск

Потрібні Docker Desktop, локальний PayPilot-стенд на `localhost:8000`,
його код у `~/paypilot/paypilot-stand` та налаштована модель
`anthropic/claude-haiku-5.5` через OpenRouter. Якщо стенд розташований
інакше, задайте `STAND_DIR` для Docker Compose. Не зберігайте ключі в
репозиторії. Ранер не фіксує модель у кейсі: перевірте її на стенді перед
прогоном.

Команди запускати послідовно з кореня HW7. Запити платні, а ранер скидає
базу перед кожним діалогом — на спільному стенді не запускати.

```bash
cd ~/paypilot/HW7
docker compose -f evaluator/docker-compose.yml run --rm eval --set l05_dialogs --gate all --profile clean
docker compose -f evaluator/docker-compose.yml run --rm eval --set l05_dialogs --gate all --profile lesson-08
```

Після завершення **або помилки** поверніть профіль і перевірте стан:

```bash
curl --fail --show-error -X PUT -H 'Content-Type: application/json' \
  --data '{"profile":"clean"}' http://localhost:8000/api/_test/profile
curl --fail --show-error http://localhost:8000/health
```

Health має показувати `profile: clean` і порожній `active_defects`.
`--gate all` включає daily та release; FAIL дає ненульовий код завершення.
Ранер записує звіти в `reports/`. Офлайн-перевірки, без запуску стенду:

```bash
cd ~/paypilot/HW7
python3 evaluator/cli.py --set l05_dialogs --dry-run
(cd evaluator && python3 -m unittest test_budget test_invariants)
```

## Бюджетні кейси

| id | Що ловить / призначення | identical_tool_calls_max | tokens_total | latency_ms | gate / runs |
| --- | --- | ---: | ---: | ---: | --- |
| `CONV-002` | 7 кроків, збереження суми та reason code; бюджет історії | — | 68000 | 35000 | daily* / 5 |
| `CONV-003` | 5 кроків, збереження суми; бюджет короткого діалогу | — | 50000 | 25000 | daily* / 5 |
| `CONV-004` | 10 кроків, збереження сутностей; бюджет довгого діалогу | — | 97000 | 50000 | daily* / 5 |
| `CONV-005` | 7 кроків без побічних тем; бюджет історії | — | 68000 | 35000 | daily* / 5 |
| `CONV-006` | 7 кроків, ізоляція кількості сутностей; бюджет історії | — | 68000 | 35000 | daily* / 5 |
| `CONV-007` | 7 кроків, контроль без згортки; бюджет історії | — | 68000 | 35000 | daily* / 5 |
| `C07-BUDGET` | D15: збільшення токенів через повторний контекст | — | 68000 | 35000 | release / 5 |
| `D14-BUDGET` | D14: повтори інструмента на порожніх транзакціях CUS-0003 | 1 | — | — | daily / 1 |

`daily*` — значення за замовчуванням ранера. Для шести `CONV-*` після
основної перевірки інваріантів ранер також обчислює окремий бюджетний
вердикт з тих самих прогонів; кейс загалом FAIL, якщо не проходить будь-який
із двох вердиктів. Пороги попередньо масштабовані за довжиною, без
індивідуальної калібрації.
Для `C07-BUDGET` перевіряються медіана токенів за прогонами й максимальна
latency. `D14-BUDGET` рахує назви інструментів на останньому кроці та не
гарантує наявність виклику; передумову перевіряє сценарій B1 `tool_called`.

## Прогони

| Що | Профіль / модель | Звіт |
| --- | --- | --- |
| набір `l05_dialogs` | clean / Haiku 5.5, 2/8 PASS (з комбінованою перевіркою бюджетів) | [clean](./reports/l05_dialogs-clean-20261028T155127.json) |
| набір `l05_dialogs` | lesson-08 / Haiku 5.5, 0/8 PASS з комбінованою перевіркою | [lesson-08](./reports/l05_dialogs-lesson-08-20261028T161418.json) |
| B4 | Haiku 5.5, Gemini 2.5 Flash | [b4.json](./l08/b4.json) |

Обидва нові звіти мають хеш набору `80bfb5184a2d`, 36 діалогів та окремі
бюджетні вердикти для всіх кейсів. На clean пройшли 2/8, на lesson-08 0/8.
Стенд після прогону lesson-08 повернуто на clean (`active_defects: []`).
B4 містить 8 комбінацій по 5 прогонів. Причини FAIL, калібрування та
обмеження висновків наведено у [звіті](./l08/observability-cost-report.md).
