# PC814: optocoupler with a bidirectional LED input

This is a behavioral QSPICE subcircuit for the Sharp PC814 electrical
characteristics at 25 °C. The photograph of the original board looks more
like a Lite-On LTV-814S with an A rank mark. The Sharp PDF provided with the
project is included in `datasheets/PC814_Sharp_series.pdf` as the traceable
source for the numerical acceptance benches. Confirm the actual manufacturer
and order code before using its isolation and temperature specifications.

```spice
.lib C:\path\to\Qlib_v1\PC814\PC814.txt
Xdet LED1 LED2 COLLECTOR EMITTER PC814 params: CTR1M=0.5
```

Pins **1, 2, 4, 3** correspond to LED, LED, collector, emitter. The LED pins
can be reversed. `CTR1M` is the current transfer ratio at IF = 1 mA,
VCE = 5 V and Ta = 25 °C, as a dimensionless fraction. The A rank is
0.5...1.5; unrestricted parts may be 0.2...3.0. Default 0.5 follows the
*representative* graph, and is not a guaranteed typical CTR.

Run in Git Bash from the library root:

```bash
./run_tests.sh PC814
```

The seven benches and their matching `.expect` files live in `tests/`.
See `tests/TEST_README.md` for individual datasheet conditions and limits.
The root `TEST_RESULTS.md` is produced by the user's runner. The bundled
`tests/TEST_RESULTS.md` records the last verified run before the naming change;
run the new `PC814` suite locally to refresh the project-level report.

## Model scope

- LEDs: fitted VF = 1.20 V at 20 mA/25 °C, two anti-parallel diode models.
- Transfer: nominal Fig. 5 shape fitted over roughly 0.5...20 mA, scaled by
  CTR1M. This curve is not a guaranteed production bound away from 1 mA.
- Saturation: behavioral soft knee near 0.1 V for IF = 20 mA/IC = 1 mA.
- Timing: a single 2 µs control-node time constant, appropriate only as an
  initial approximation. Phototransistor storage after saturation is absent.
- Dark current: a fixed 1 GΩ output leakage (20 nA at VCE = 20 V).
- Temperatures: the SPICE diode has intrinsic scaling, but output CTR,
  storage, leakage, and optomechanical isolation are **not validated** versus
  temperature. Never extrapolate the 25 °C CTR to -35 °C without measurements.
- Isolation: input and output are separate controlled networks in a simulator;
  5 kVrms/creepage are real part/package/PCB specifications, not simulation
  properties.

An early draft had the polarity of `Boptical` reversed.
It would charge the control node negative and keep the transistor off. This
library revision reverses that source and makes the CTR bench catch a repeat.

The S2000 input resistors (24 kΩ + 24 kΩ per channel at mains voltage) belong
to the **device project**, not these component tests. Approximate dissipation
is 0.55 W in *each resistor* at 230 Vrms and 0.67 W at 253 Vrms; those values
must be checked against the actual resistor rating and thermal layout.

## Datasheet provenance

- Sharp, *PC814 Series, AC Input Photocoupler*, attached PDF in `datasheets/`.
- Likely installed alternative: Lite-On, *LTV-8X4 series Rev. L*:
  https://optoelectronics.liteon.com/upload/download/DS-70-96-0013/LTV-8X4%20series%20%20Rev.L.PDF
  The Lite-On PDF was not supplied as a local file; this is an external
  reference for subsequent part identification, not an attached datasheet.
