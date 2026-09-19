# IRF7309 — 0.1.0-preview

Комплементарная пара International Rectifier / Infineon: N-channel 30 В и
P-channel −30 В в SO-8. Файлы содержат отдельные QSPICE VDMOS-модели каналов.

```spice
.lib IRF7309_N.txt
.lib IRF7309_P.txt
M_N drain gate source source IRF7309_N
M_P drain gate source source IRF7309_P
```

Исходные строки `irf7309n-ltspice` и `irf7309p-ltspice` взяты из
`KSKelvin-Github/Qspice`, файл `SPICE-MOSFET-KSKelvin.lib`, commit
`c51d63f46173b16c4b5c309ac31e081df6b4c5e4`. Идентификаторы нормализованы.
Лицензия исходника — GPL-3.0; её копия находится в `LICENSE-GPL-3.0.txt`.

## Этап 1: статическая приёмка

Первый стенд проверяет при 25 °C отдельно для N- и P-канала:

- `VGS(th)` при `VDS=VGS`, `|ID|=250 мкА`;
- `RDS(on)` при `|VGS|=10 В`;
- `RDS(on)` при `|VGS|=4,5 В`;
- утечку сток–исток при `|VDS|=24 В`, `VGS=0`;
- прямое падение body diode при `|IS|=1,8 А`.

```bash
KEEP_RAW=1 ./run_tests.sh IRF7309
```

Dead-time, complementary half-bridge, частотный sweep, capacitance и gate
charge намеренно отложены до завершения базовой статической приёмки.

## Ограничения

Параметры `RON`, `QG`, `VDS` и `MFG` в VDMOS являются метаданными и сами по
себе не доказывают соответствие даташиту. У модели нет явно заданных и
проверенных температурных коэффициентов `VGS(th)` и `RDS(on)`, тепловой RC-сети,
корпусных индуктивностей, avalanche/разрушения и статистического разброса.
Поэтому расчёт потерь при повышенной температуре пока нельзя считать
даташитно верифицированным.
