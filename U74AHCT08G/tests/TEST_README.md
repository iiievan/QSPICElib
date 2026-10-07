# U74AHCT08G tests

Run `./run_tests.sh U74AHCT08G/` from the library root.

| Bench | Condition | Checks |
| --- | --- | --- |
| `U74AHCT08G_dc_test` | VCC=4.5V; full 14-pin wrapper; 0/3.3V truth table, 8mA high/low loads; 0.8/2.0V boundary inputs | AND outputs, 3.3V MCU compatibility, guaranteed VOH>=3.94V and VOL<=0.36V are HARD |
| `U74AHCT08G_timing_test` | VCC=5V, input rise/fall 3ns, CL=15/50pF, both A and B | tPLH/tPHL typical 5/5.5ns WARN; 6.9/7.9ns published maxima HARD, at 25 C |

The model's one nominal input threshold does not reproduce the datasheet's
undefined 0.8..2.0V region. The datasheet gives -40..+125 C operation,
but this model's device timing has no temperature dependence. A successful
run establishes only the stated 25 C model benches. See `TEST_RESULTS.md`.
