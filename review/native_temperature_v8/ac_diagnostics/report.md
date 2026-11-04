# Локализация сбоя MEXTRAM

Диагностические варианты не устанавливаются и не дают приёмки модели. Вариант 5pin_clamped имеет фиксированный DT=0 и условные RTH/CTH исключительно для проверки структуры теплового узла.
Переходная оценка на одном шаге/амплитуде служит диагностике; проверки сходимости для принятия Cobo здесь нет.

| Случай | Анализ | Топология | Движок | Статус | C, pF (диагностика) |
|---|---|---|---|---|---:|
| gp_ac | ac | 3pin | 64 | COMPLETED_DIAGNOSTIC_ONLY | 60.0002994610429 |
| gp_tran | tran | 3pin | 64 | COMPLETED_DIAGNOSTIC_ONLY | 60.00141354564454 |
| mex_op | op | 3pin | 64 | COMPLETED_DIAGNOSTIC_ONLY | — |
| mex_dc | dc | 3pin | 64 | COMPLETED_DIAGNOSTIC_ONLY | — |
| mex_ac3 | ac | 3pin | 64 | FAILED | — |
| mex_tran3 | tran | 3pin | 64 | COMPLETED_DIAGNOSTIC_ONLY | 89.68603860148632 |
| mex_ac4 | ac | 4pin | 64 | FAILED | — |
| mex_ac5_clamped | ac | 5pin_clamped | 64 | FAILED | — |
| mex_tran5_clamped | tran | 5pin_clamped | 64 | FAILED | — |
| mex_ac3_other | ac | 3pin | 80 | FAILED | — |
| mex_ac4_other | ac | 4pin | 80 | FAILED | — |
| mex_ac5_clamped_other | ac | 5pin_clamped | 80 | FAILED | — |
