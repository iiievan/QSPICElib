# PC814: acceptance bench map

**Source:** bundled Sharp PC814 Series PDF; numbered pages below refer to
PDF pages (1-based). All electrical acceptance points are at Ta = 25 °C.
The user ran the same seven benches under the previous `AC814` names in
QSPICE/QPOST; all 24 checks passed. The model and tests were subsequently
renamed to `PC814` without changing their electrical statements or expected
values. Run `./run_tests.sh PC814/` for a fresh QSPICE record. See
[`TEST_RESULTS.md`](TEST_RESULTS.md) and the original console log.
The photo hints at Lite-On LTV-814S/A, for which the exact datasheet has not
yet been attached. Device identification is a separate check.

| Bench | Datasheet location / precise condition | Assertions |
|---|---|---|
| `PC814_led_test` | p. 2, VF at IF = ±20 mA, 25 °C | typical 1.2 V WARN; guaranteed max 1.4 V HARD; LED symmetry HARD; 1 mA Fig. 4 WARN |
| `PC814_ctr_test` | p. 2, CTR at IF = ±1 mA, VCE = 5 V | 20...300% unrestricted and 50...150% A rank, model corner sweep +/− HARD |
| `PC814_ctr_curve_test` | p. 3, Fig. 5, VCE = 5 V, 25 °C | approximate graph readings at 0.5/1/2/5/10/20 mA; WARN only |
| `PC814_output_test` | p. 2, VCE(sat) at IF = ±20 mA, IC = 1 mA; ICEO at 20 V/IF = 0 | sat max 0.2 V and dark max 100 nA HARD; ~0.1 V typical is a diagnostic |
| `PC814_capacitance_test` | p. 2, Ct at 0 V and f = 1 kHz | AC sweep from 100 Hz to 10 kHz; FIND at 1 kHz; 50 pF typical WARN, 250 pF max HARD |
| `PC814_timing_test` | p. 2, rise/fall at VCE ~2 V, IC ~2 mA, RL = 100 Ω | 18 µs upper limits HARD, 4/3 µs typical WARN; load chosen to approximate datasheet fixture |
| `PC814_ac_input_test` | additional component-level functional test | 50 Hz + and − half-waves must pull the 3.3 V output low and release at zero crossing; HARD |

## Reading the results

`*.expect` uses the same `abs`, `min`, `max` and `warn_abs` modes as the rest
of this library. Each named measurement has its own QPOST record. GUARANTEED
min/max at the specified conditions are hard limits. Typical numbers and
values digitized from a plot are only WARN diagnostics; the graph is not a
statistical production limit.

The CTR `.step` tests intentionally assign the nominal 1 mA ratio to a
model instance; their close tolerance checks wiring/sign and parameter
passing. They do **not** prove that any physical sample has that exact CTR.
The response test does not emulate saturation storage and the fast transitions
may depend on the selected load. A model that passes the 25 °C suite is not
qualified for continuous mains isolation or −35 °C operation.

No QSPICE/QPOST executable is available in the authoring environment, so the
acceptance record is based on the user-provided real run under the previous
names. The project-level `TEST_RESULTS.md` remains the previously generated
record; the `PC814` naming has not yet been exercised in a new simulator run.
