# YJM05GP06A / YJM05N06A bench map

Run from library root: `./run_tests.sh YJM05G06A/`.
The Kexin IRLML0100 results do not validate this Yangjie pair.

| Bench | Source and condition | Interpretation |
| --- | --- | --- |
| `YJM05G06A_dc_test` | Both datasheets p.2, TJ=25 C: threshold at 250uA, RDS(on) at 10/4.5V and 5/4A, IDSS at 60V, diode at 5A | Published min/max are HARD; typical RDS(on) is WARN |
| `YJM05G06A_capacitance_test` | Both p.2, 1MHz, 30V | Six typical Ciss/Coss/Crss points WARN; 1-ohm AC sensors |
| `YJM05G06A_gate_charge_test` | P Rev.1.0: 10V, 30V, 5A; N Rev.3.2: 10V, 30V, 10A | Typical Qg and Qgd WARN; 1mA gate current, time in us equals charge in nC |
| `YJM05G06A_switching_test` | Both 30V, 10V gate, P 5A/2.2ohm RG, N 2A/3ohm RG | Typical delay and transition times WARN; N targets from Rev.3.2 |
| `YJM05G06A_frequency_4v5_test` | 24V, 1A half-bridge, 250ns commanded dead time, 20/100/400kHz, -35/25/60 C | Rail reach HARD in the model fixture, times WARN |
| `YJM05G06A_frequency_10v_test` | Same at 10V gate drive | Same model-only characterization |

The *attached older* N Rev.2.0 switching row simultaneously lists
VDD=30V, ID=2A and RL=1ohm; 30V/1ohm is 30A for a simple resistor. The
new Rev.3.2 no longer gives that load resistance. The bench uses 15ohm to
match 2A and treats timing as a diagnostic, not a guaranteed limit. The
frequency benches use ideal gate drive and do not establish an
allowable PWM frequency, dead time, junction temperature or motor current.

P values came from the attached PDF; N dynamic values from the newer
manufacturer revision linked in `../README.md`. QSPICE/QPOST has not yet
been run on these new benches; see `TEST_RESULTS.md`.
