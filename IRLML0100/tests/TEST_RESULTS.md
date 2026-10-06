# IRLML0100: verification status

The user's first QSPICE/QPOST run of the original four benches completed with
**17 PASS, 0 WARN, 0 FAIL**. The console output is archived as
`IRLML0100_first_run.log` in this directory. Those results cover
`IRLML0100_dc_test`, `IRLML0100_capacitance_test`,
`IRLML0100_gate_charge_test`, and `IRLML0100_3v3_switch_test`.

The new `IRLML0100_switching_test`, `IRLML0100_frequency_3v3_test`, and
`IRLML0100_frequency_4v5_test` are **pending their first QSPICE/QPOST run**.
They were added after that log was produced. From the library root execute
`./run_tests.sh IRLML0100/` and review the resulting project-level
`TEST_RESULTS.md`, including any WARN for typical switching times and
model-only frequency and temperature behavior.
