# Cobo через малосигнальный переходный анализ

**TRAN_COBO_CALIBRATED_AND_CONVERGED**

VCB=10 V, 1 MHz, эмиттер через 1 TΩ. Измеряется квадратурная составляющая тока при синусе: C = -2·integral(Iист·cos(wt))/(T·A·w). Используется реальный QPOST TRAN.
Проверяются три шага, две амплитуды и два временных окна. Для каждой разницы требуется ≤0.5%; GP-контроль должен совпасть с прежним нативным AC в пределах 2%.
Этот результат имеет метод TRAN-admittance. Нативный .ac не выполняется, его сбой этим не исправлен и не объявляется PASS. Тепловые параметры и тип модели не заменяются.

## gp

| Случай | Cobo, pF | Статус |
|---|---:|---|
| step_5ns | 59.9998 | MEASURED |
| step_2p5ns | 60.0001 | MEASURED |
| step_1p25ns | 60.0001 | MEASURED |
| half_amplitude | 60.0003 | MEASURED |
| late_window | 60.0001 | MEASURED |

Сходимость: `{'pass': True, 'relative_checks': {'step_5_to_2p5': 5.305152363965782e-06, 'step_2p5_to_1p25': 0.0, 'amplitude': 2.1220609456307216e-06, 'settling': 2.220446049250313e-16, 'GP_vs_previous_AC': 7.957707437666883e-06}, 'Cobo_F': 6.000026763005428e-11}`.

## initial

| Случай | Cobo, pF | Статус |
|---|---:|---|
| step_5ns | 89.6838 | MEASURED |
| step_2p5ns | 89.6841 | MEASURED |
| step_1p25ns | 89.6841 | MEASURED |
| half_amplitude | 89.6844 | MEASURED |
| late_window | 89.6841 | MEASURED |

Сходимость: `{'pass': True, 'relative_checks': {'step_5_to_2p5': 3.5492331881270545e-06, 'step_2p5_to_1p25': 0.0, 'amplitude': 3.549233188016032e-06, 'settling': 1.1102230246251565e-16}, 'Cobo_F': 8.968444705205538e-11}`.

## final

| Случай | Cobo, pF | Статус |
|---|---:|---|
| step_5ns | 60.0393 | MEASURED |
| step_2p5ns | 60.0393 | MEASURED |
| step_1p25ns | 60.0396 | MEASURED |
| half_amplitude | 60.0396 | MEASURED |
| late_window | 60.0396 | MEASURED |

Сходимость: `{'pass': True, 'relative_checks': {'step_5_to_2p5': 0.0, 'step_2p5_to_1p25': 5.3016647226789004e-06, 'amplitude': 0.0, 'settling': 2.220446049250313e-16}, 'Cobo_F': 6.00396107319866e-11}`.
