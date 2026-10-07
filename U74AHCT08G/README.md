# UTC U74AHCT08G-UEA-R quad AND gate

Initial QSPICE behavioral model based on the attached UTC PDF
`U74AHCT08G-UEA-R.pdf` (QW-R502-415.D). The provided `-UEA-R` part is
SOP-14U. Each `U74AHCT08_G` instance represents one powered two-input AND;
the `U74AHCT08` wrapper is the complete chip with the **physical pin 1..14
order** specified in the datasheet. Include `U74AHCT08G.txt` once.

```spice
.lib U74AHCT08G/U74AHCT08G.txt
Xone A B Y VCC GND U74AHCT08_G
Xquad 1A 1B 1Y 2A 2B 2Y GND 3Y 3A 3B 4Y 4A 4B VCC U74AHCT08
```

At VCC=4.5..5.5V, a 3.3V MCU HIGH exceeds the 2.0V guaranteed input
high level. The model uses a nominal 1.4V input transition, not a guarantee
of logic behavior in the undefined 0.8..2.0V region. It fits 8mA output
VOH/VOL and typical 5V propagation roughly; it omits supply current,
input clamps, ESD, detailed output-current nonlinearity and temperature
dependence. The input-to-output path is an AND function, as confirmed by
the datasheet truth table. No QSPICE run has yet validated this first fit.

The chip supplies a logic-level output; use a separate gate-drive and
power-stage simulation for the actual S2000 MOSFET gate network and check
the output current and waveform at its real load.
