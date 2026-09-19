# Протокол сверки FDS4559

| | |
|---|---|
| Дата | 18.09.2026 |
| Модели | `FDS4559_N.txt`, `FDS4559_P.txt` |
| Даташит | onsemi FDS4559/D Rev. 4, July 2022 |
| Стенды | 25 °C DC + 125 °C temperature anchor |
| Regression | **PASS 20 / WARN 2 / FAIL 0** |
| Hard requirements | **все прошли** |

## Итоговая DC-модель

После QSPICE MOSFET Model Generator выполнены две документированные
калибровки:

- `Rext/(Rext+Rchannel) = 0.90` для N и `0.88` для P;
- `Trs1=Trd1=7.4m` для N и `6.0m` для P.

Они исправили low-gate-drive `RDS(on)` и температурную зависимость, не
сломав основные 25 °C точки.

## Результаты 25 °C

| Измерение | Измерено | Datasheet | Вердикт |
|---|---:|---:|---|
| N1 `VGS(th)` typ | 2.58298 V | 2.2 V typ | **WARN**; Min/Max PASS |
| N2 `RDS(on)`, 10 V | 42.02 mΩ | 42 typ / 55 max mΩ | PASS |
| N3 `RDS(on)`, 4.5 V | 56.73 mΩ | 55 typ / 75 max mΩ | PASS |
| N4 body diode | 0.76475 V | 0.8 typ / 1.2 max V | PASS |
| P1 `|VGS(th)|` typ | 1.89309 V | 1.6 V typ | **WARN**; Min/Max PASS |
| P2 `RDS(on)`, −10 V | 82.03 mΩ | 82 typ / 105 max mΩ | PASS |
| P3 `RDS(on)`, −4.5 V | 105.99 mΩ | 105 typ / 135 max mΩ | PASS |
| P4 body diode | 0.78733 V | 0.8 typ / 1.2 max V | PASS |

## Температурный anchor 125 °C

| Измерение | Измерено | Datasheet | Вердикт |
|---|---:|---:|---|
| N `RDS(on)`, 10 V | 72.07 mΩ | 72 typ / 94 max mΩ | PASS |
| P `RDS(on)`, −10 V | 130.22 mΩ | 130 typ / 190 max mΩ | PASS |

Обе оставшиеся WARN-точки — `VGS(th)` typical. Они находятся внутри
гарантированных Min/Max; модель специально не подгонялась по `Vto`, чтобы не
ухудшить силовые характеристики.

## Application half-bridge

Добавлен `FDS4559_halfbridge_test.cir`: 24-V полумост с реальными моделями
`74HCT244 + ULQ2003A + FDS4559`, уровнями M032 3.3 V и токами 0.15 / 0.50 A
при `-35 / +25 / +60 °C`.

**Первый прогон application-стенда ещё не выполнен.** После запуска сюда
следует добавить падения на ключах, gate-drive margins и `ULQ VCE(sat)`.
