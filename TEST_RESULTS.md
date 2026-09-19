# QSPICElib — test results

> Stored validation snapshot from 2026-09-19. `./run_tests.sh` replaces this
> file with the measurements and verdicts from the next actual QSPICE run.

## Component capability summary

| Component | Verified capability | Current result |
|---|---|---|
| `74HCT244` | VOH/VOL, propagation delay, output transition time and three-state enable/disable timing in four datasheet corners | **PASS: 8 measurements × 4 corners** |
| `ULQ2003A` | VCE(sat) at 100/200/350 mA, input current, turn-on/off time and clamp-diode voltage at Typ/Max corners | **PASS: 7 measurements × 2 corners** |
| `FDS4559` | N/P threshold, RDS(on) at both gate drives, body-diode drop at 25 °C, and RDS(on) anchor at 125 °C | **PASS 20 / WARN 2 / FAIL 0** |
| `FDS4559` half-bridge | Functional switching at 20/100/400 kHz and −55/+25/+175 °C in the generic 30 V / 1 A fixture | **PENDING: first QSPICE run required** |

The two validated FDS4559 WARNs are nominal `VGS(th)` fits. Both models remain
inside the guaranteed Min/Max bounds; details are in
`FDS4559/tests/TEST_RESULTS.md`.

## Important limits

- The FDS4559 datasheet does not specify a maximum PWM frequency across its
  full junction-temperature range. The frequency bench is a model-functional
  check, not a hardware frequency guarantee.
- FDS4559 `Qrr/trr`, thermal RC/self-heating, avalanche, package/PCB
  inductance, ringing, EMI and the real gate driver are not validated.
- ULQ2003A temperature behavior is not represented by its behavioral model;
  its electrical table has no temperature columns.
- 74HCT244 temperature behavior is represented by explicit datasheet corners,
  not by the simulator's `.temp` physics.

Project-specific supply rails, loads, drivers, dead time, cooling and pass/fail
criteria belong to the final device repository.
