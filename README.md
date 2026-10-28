# HW7 · Observability & Cost Report

Домашнє завдання для PayPilot. Замість L03 використано адаптований ранер
із HW4, оскільки локальний L03 не підтримував assertions набору та потрібний
підрахунок токенів діалогу.

## L08 · Observability & Cost Report

- [Заповнений звіт](./l08/observability-cost-report.md) і
  [порожній шаблон](./l08/observability-cost-report.template.md).
- [Діалогові кейси з бюджетами](./sets/l05_dialogs.jsonl).
- [Журнал виконання](./HW7.md), [модель вартості](./l08/cost-model.md),
  [B4](./l08/b4.json) і [результати прогонів](./reports/).

### Запуск

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

### Бюджетні кейси

| id | Що ловить / призначення | identical_tool_calls_max | tokens_total | latency_ms | gate / runs |
| --- | --- | ---: | ---: | ---: | --- |
| `CONV-002` | 7 кроків, збереження суми та reason code; бюджет історії | — | 61241 | 35000* | release / 5 |
| `CONV-003` | 5 кроків, збереження суми; бюджет короткого діалогу | — | 35492 | 25000* | release / 5 |
| `CONV-004` | 10 кроків, збереження сутностей; бюджет довгого діалогу | — | 92834 | 50000* | release / 5 |
| `CONV-005` | 7 кроків без побічних тем; бюджет історії | — | 55361 | 35000* | release / 5 |
| `CONV-006` | 7 кроків, ізоляція кількості сутностей; бюджет історії | — | 65774 | 35000* | release / 5 |
| `CONV-007` | 7 кроків, контроль без згортки; бюджет історії | — | 63081 | 35000* | release / 5 |
| `C07-BUDGET` | D15: збільшення токенів через повторний контекст, L2 | — | 67000 | 35000* | release / 5 |
| `D14-BUDGET` | D14: L1 один `get_transactions` + ліміт однакових викликів | 1 | — | — | daily / 1 |

Токенні пороги обчислено окремо для кожного кейсу за clean-медіанами;
`C07-BUDGET` має 67000: 13.11% запасу над фінальною clean-медіаною 59233,
а lesson-08-медіана 74657 її перевищує. Зірочкою позначені latency SLA,
які ще потрібно звірити/перекалібрувати. Для шести `CONV-*` після
основної перевірки інваріантів ранер також обчислює окремий бюджетний
вердикт з тих самих прогонів; кейс загалом FAIL, якщо не проходить будь-який
із двох вердиктів. На `release` виконується п'ять разів; D14 на `daily` —
один раз. `D14-BUDGET` перевіряє наявність рівно одного виклику інструмента
як L1 і окремо обмежує повторення однакових викликів. `C07-BUDGET` є
L2 token-budget перевіркою: медіана п'яти діалогів, D15; latency перевіряється
за тимчасовим SLA.

### Прогони

| Що | Профіль / модель | Звіт |
| --- | --- | --- |
| набір `l05_dialogs` | clean / Haiku 5.5, 2/8 PASS | [clean](./reports/l05_dialogs-clean-20261028T194534.json) |
| набір `l05_dialogs` | lesson-08 / Haiku 5.5, 0/8 PASS | [lesson-08](./reports/l05_dialogs-lesson-08-20261028T192032.json) |
| B4 | Haiku 5.5, Gemini 2.5 Flash | [b4.json](./l08/b4.json) |

Обидва останні звіти мають hash `49811ba93c4e`, по 36 діалогів, та
`gate: all`. У clean `CONV-005` завершився помилкою HTTP 500, тому ранер
не зміг сформувати для нього budget verdict; решта бюджетних кейсів мають
окремі вердикти. Раніші звіти зберігають попередні запуски й калібрування.
Після прогонів стенд повернуто на clean (`active_defects: []`).
B4 містить 8 комбінацій по 5 прогонів. Причини FAIL, калібрування та
обмеження висновків наведено у [звіті](./l08/observability-cost-report.md).
