# IRLML0100: bench map

Source: attached Kexin IRLML0100 (KRLML0100) PDF, pp. 1–3. The user ran
all seven benches: 37 PASS, 0 WARN, 0 FAIL. The original four-bench run
reported 17 PASS, 0 WARN, 0 FAIL. See `TEST_RESULTS.md` and the saved logs.

## Static and component characterization

| Bench | Condition | Criteria |
| --- | --- | --- |
| `IRLML0100_dc_test` | p. 2, Ta = 25 °C: ID = 250 µA/VDS = VGS for threshold; 1.3 A at 4.5 V and 1.6 A at 10 V for RDS(on); body diode at 1.1 A; leakage at 100 V | Min/max threshold, RDS(on), diode and leakage HARD; RDS(on) typical WARN; 3.3 V/5 mA diagnostic WARN |
| `IRLML0100_capacitance_test` | p. 2, VGS = 0, VDS = 25 V, 1 MHz | 290/27/13 pF Ciss/Coss/Crss typical WARN; measure current via 1 Ω sensors |
| `IRLML0100_gate_charge_test` | p. 2, VGS = 4.5 V, VDS = 50 V, ID = 1.6 A | Qg 2.5 nC and Qgd 1.2 nC typical WARN; 1 mA gate current turns ns of charge into µs |

## Switching and frequency behavior

| Bench | Condition | Criteria |
| --- | --- | --- |
| `IRLML0100_3v3_switch_test` | Initial single pulse, 24 V and 4.7 kΩ load, 3.3 V gate | Functional on/off at 25 °C, already passed |
| `IRLML0100_switching_test` | Kexin p. 2/Fig. 10: 4.5 V gate, 50 V drain, 1 A, 6.8 Ω external gate resistance, 25 °C | 2.2/2.1/9/3.6 ns typical timing WARN only |
| `IRLML0100_frequency_3v3_test` | 24 V, 4.7 kΩ, 100 Ω gate resistance; 20/100/400 kHz × −35/25/60 °C | Gate and drain rail reach HARD in the model; command-to-rail times diagnostic WARN |
| `IRLML0100_frequency_4v5_test` | Same fixture at 4.5 V gate drive | Same checks; no frequency rating inferred from a pass |

In `*.expect`, guaranteed tabulated min/max limits are HARD. Typical points
and figure-derived/application behavior are only WARN except the structural
rail reach invariant of the 3.3 V fixture. No simulator pass establishes
behavior of physical devices across temperature, board layout, or gate-drive
transients. Treat the 3.3 V and temperature sweeps as circuit hypotheses
until samples are measured at the intended temperature and pull-up load.
