# EL354NA: acceptance bench map

Source: Everlight *EL354N-G Series*, Rev. 6 PDF in `../datasheets/`.
Pages refer to PDF page numbers (1-based). Tests operate at Ta = 25 °C.
The user ran the full suite in QSPICE/QPOST on 2026-10-06: 22 PASS, 0 WARN,
0 FAIL. See [`TEST_RESULTS.md`](TEST_RESULTS.md) and `EL354NA_pass_run.log`.

| Bench | Source and condition | Checks |
| --- | --- | --- |
| `EL354NA_led_test` | p. 3 VF at IF = ±20 mA; p. 4 Fig. 1 | 1.2 V typical WARN, 1.4 V maximum HARD, direction symmetry HARD, 1 mA graph diagnostic WARN |
| `EL354NA_ctr_test` | p. 3 EL354NA CTR 50–150% at IF = ±1 mA, VCE = 5 V | A-rank corners 0.5/1/1.5 and polarity symmetry HARD |
| `EL354NA_ctr_curve_test` | p. 4 Fig. 3, VCE = 5 V | Approximate 0.5/1/2/5/10/20/30 mA curve WARN only |
| `EL354NA_output_test` | p. 3 VCE(sat) at IF = 20 mA, IC = 1 mA; ICEO at VCE = 20 V | Saturation max 0.2 V and dark current max 100 nA HARD; 0.1 V typical WARN |
| `EL354NA_capacitance_test` | p. 3 Cin at 0 V, 1 kHz | 50 pF typical WARN, 250 pF max HARD, using 1 V AC |
| `EL354NA_timing_test` | p. 3 tr/tf at VCE ≈ 2 V, IC ≈ 2 mA, RL = 100 Ω | 18 µs upper limits HARD; on-level diagnostic WARN |
| `EL354NA_ac_input_test` | component functional check, not datasheet limit | Both 50 Hz polarities switch a 3.3 V, 10 kΩ pull-up HARD |

The numerical `CTR1M` sweep checks that pin directions, sign and parameter
passing are correct. It does not demonstrate that every production sample
has a particular CTR. Figure readings are typical diagnostics, not
production limits. No 25 °C test certifies operation across temperature.
The simulator must be run by the user; the authoring environment only tests
the runner parser and static bench wiring.
