# EL354NA — Everlight AC-input phototransistor optocoupler

QSPICE behavioral subcircuit for the warehouse part **EL354N(A)(TA)-VG**.
The A suffix identifies the 50–150% CTR rank at IF = ±1 mA, VCE = 5 V,
Ta = 25 °C. `TA` describes reel packing, and `-VG` is the ordering option;
these suffixes do not change the simulated electrical pins. Primary source:
the user-supplied Everlight *EL354N-G Series* Rev. 6 PDF in `datasheets/`.

```spice
.lib C:\path\to\Qlib_v1\EL354NA\EL354NA.txt
Xdet LED1 LED2 COLLECTOR EMITTER EL354NA params: CTR1M=0.5
```

Pins 1 and 2 are the interchangeable antiparallel LED terminals; pin 4 is
collector and pin 3 emitter. `CTR1M` specifies IC/IF at 1 mA, 5 V and 25 °C.
Its default 0.5 is the **low A-rank corner**, not a stated typical value.
For nominal exploration set `CTR1M=1`; also simulate 0.5 and 1.5.

From the library root in Git Bash:

```bash
./run_tests.sh EL354NA/
```

Seven independent `.cir` benches and `.expect` limits are in `tests/`.
The model and bench mapping are documented in `tests/TEST_README.md`.
`tests/TEST_RESULTS.md` records the user's passing QSPICE run (22 PASS,
0 WARN, 0 FAIL) and includes the original console log.

## Scope and limits

- Antiparallel LEDs: VF fitted near 1.2 V at IF = ±20 mA, 25 °C.
- CTR: exact assigned `CTR1M` at 1 mA/5 V/25 °C; current dependence is an
  approximate fit to Everlight's **Fig. 3**, over about 0.5–30 mA.
- Output: approximate saturation knee near 0.1 V at the specified test
  condition, fixed 1 GΩ dark-current path (20 nA at VCE = 20 V).
- Switching: one 2 µs optical time constant is illustrative. It omits
  phototransistor charge storage and is checked only against the stated
  18 µs maximum under the datasheet fixture conditions.
- Temperature: only the diode's intrinsic SPICE temperature scaling is
  present. The output CTR, leakage and storage do **not** track the
  temperature curves in Fig. 4. In particular, no conclusion about detection
  at −35 °C follows from a 25 °C pass. Measure samples and adjust the model.
- Isolation, 3750 Vrms test rating, operating voltage, creepage and clearance
  are properties of the physical part and PCB; this electrical subcircuit
  cannot validate them. The SOP-4 footprint differs from DIP-4 PC814.
- The model does not simulate the 80 V collector breakdown or absolute
  maximum input power; do not use it to qualify those limits.

The existing `PC814` model and its actual passing QSPICE log are preserved.
This separate model uses Everlight's own datasheet and independent tests.
