# Yangjie YJM05GP06A / YJM05N06A complementary pair

Initial QSPICE VDMOS fits based on Yangjie datasheets: P-channel
`YJM05GP06A.pdf` Rev.1.0 (2022) as attached, and N-channel Yangjie's
[Rev.3.2 (2022)](https://www.21yangjie.com/pdf/mosfet/YJM05N06A.pdf).
The attached `YJM05N06A.PDF` is the older Rev.2.0 (2019). These are
**candidate** models and have not yet been fitted to real QSPICE
results or physical devices. Include the two files separately:

```spice
.lib YJM05G06A/YJM05GP06A.txt
.lib YJM05G06A/YJM05N06A.txt
MP drain gate source source YJM05GP06A
MN drain gate source source YJM05N06A
```

`M` uses drain–gate–source–bulk order. Both PDFs show a SOT-223 package
drawing but **do not establish the electrical 1/2/3/tab mapping** in the
supplied pages. Confirm pinout against a manufacturer drawing or a sample
before connecting the Altium symbol and footprint. They must not be assumed
pin-compatible from their similar package outlines.

Both parts have a 60 V drain-source rating and RDS(on) limits at 4.5 and
10 V gate drive. The P-channel datasheet lists 1 W and RthJA up to 120 C/W
under its stated pad/ambient conditions, while the N-channel datasheet lists
2.5 W and 50 C/W under its own different conditions. The QSPICE models have
no electrothermal feedback, avalanche, reverse-recovery or board parasitics;
their temperature behavior is an illustrative sensitivity sweep, not
qualification for -35..+60 C service.

**N-channel revision difference:** Rev.2.0 gives Ciss/Coss/Crss
800/72/38 pF, Qg/Qgd 15/2.5 nC at ID=5 A and switching
td(on)/tr/td(off)/tf 5/39/19/7 ns. Yangjie's newer Rev.3.2 gives
1018/70/62 pF, 26/6.5 nC at ID=10 A and 10/20/29/21 ns. Static RDS(on)
limits remain 44/49 mOhm. The model and WARN targets use **Rev.3.2** to
avoid underestimating the current published gate charge. The old PDF remains
in `datasheets/` for revision traceability. Confirm which revision describes
the warehouse lot; do not combine the two sets of dynamic numbers.

The frequency benches use ideal gate sources, 6-ohm gate resistors and a
1 A resistive load on 24 V. The actual S2000 upper P gate uses an IRLML0100
pull-down and resistor network; that gate waveform, shoot-through,
freewheeling, shunt, motor and 24 V supply must be simulated separately.
See `tests/TEST_README.md` for all six benches and their acceptance rules.
