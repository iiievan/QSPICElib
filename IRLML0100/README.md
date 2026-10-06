# IRLML0100 — Kexin N-channel MOSFET candidate

This is a compact QSPICE VDMOS **initial fit**, based on the user-supplied
Kexin *IRLML0100 (KRLML0100)* 5-page datasheet in `datasheets/`.
The warehouse marking/manufacturer must be confirmed. Kexin and Infineon
publish similarly named parts; do not silently substitute one manufacturer's
characterization for another's production lot.

```spice
.lib C:\path\to\Qlib_v1\IRLML0100\IRLML0100.txt
M1 drain gate source source IRLML0100
```

Physical SOT-23 pin order in the attached Kexin PDF: **1 gate, 2 source,
3 drain**. QSPICE `M` element netlist order is **drain gate source bulk**.

Run from the library root in Git Bash:

```bash
./run_tests.sh IRLML0100/
```

Four baseline fixtures cover 25 °C tabulated DC limits and typical points,
1 MHz capacitance, gate charge, and a 24 V / 5 mA switch driven from 3.3 V.
Three further benches cover tabulated switching conditions and 20/100/400 kHz
operation with 3.3 V and 4.5 V gate drive at three application temperatures.
See `tests/TEST_README.md` for conditions and the distinction between hard
limits and typical WARN diagnostics. `tests/TEST_RESULTS.md` records the
seven-bench QSPICE run with 37 PASS and no WARN or FAIL, and retains its log.

## Limits for our S2000 circuit

- The attached datasheet guarantees RDS(on) only at VGS = **4.5 V** and
  **10 V**, at ID = 1.3 A and 1.6 A respectively. VGS(th) = 1–2.5 V is
  measured at just 250 µA; it does **not** guarantee low resistance at 3.3 V.
- Pulling a 24 V node through 4.7 kΩ needs about 5 mA, far below the
  datasheet's 1.3 A RDS(on) test. The 3.3 V fixture is a useful first
  simulation, not a production guarantee over corners and temperature.
- The package's continuous-current and 1.3 W figures assume the specified
  copper area and temperature. This model has no self-heating or board
  thermal network. It does not qualify power dissipation at the intended
  footprint, inductive avalanche, SOA, or turn-off transients.
- Gate capacitances and Qg are typical only. Cgd versus VDS is an
  approximate VDMOS fit; reverse recovery and temperature coefficients have
  not yet been calibrated against physical samples or the plotted curves.

The model is intentionally stored separately from bridge power MOSFETs.
After a real QSPICE run, adjust the fit if the capacitance/charge/diode
WARN diagnostics disagree, while preserving datasheet test conditions.
