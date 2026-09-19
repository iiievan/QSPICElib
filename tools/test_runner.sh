#!/usr/bin/env bash
# Parser/checker unit tests. Synthetic records, NOT semiconductor simulations.
# Requires only Bash and awk, as does run_tests.sh.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
task_tmp="$(mktemp -d "$root/runner-test.XXXXXX")"
trap 'rm -rf "$task_tmp"' EXIT
for block in PARSE_AWK CHECK_AWK; do
    awk -v block="$block" '
        index($0, "read -r -d") && index($0, block) { active=1; next }
        active && $0=="AWK" { exit }
        active { print }
    ' "$root/run_tests.sh" > "$task_tmp/$block.awk"
    test -s "$task_tmp/$block.awk"
done
count=0
check() {
    if [ "$2" != "$3" ]; then
        printf 'FAIL %s\nExpected: %s\nActual: %s\n' "$1" "$3" "$2"
        exit 1
    fi
    count=$((count+1))
    printf 'PASS %s\n' "$1"
}
parse() { awk -f "$task_tmp/PARSE_AWK.awk"; }
compare() { awk -f "$task_tmp/CHECK_AWK.awk" "$task_tmp/case.expect" -; }

check scalar "$(printf '.meas tran a avg V(a):\n1.25e-3\n' | parse)" 'A|1.25e-3'
check tran_extremum "$(printf '.meas tran a min V(a):\n  0.250143 (at Time=1.58028e-06)\n' | parse)" 'A|0.250143'
check steps "$(printf '.meas dc t avg V(t):\n1 -35\n2 0\n3 25\n' | parse)" 'T|-35 0 25'
check ac_real "$(printf '.meas ac g find abs(V(out)):\n( 1.2e+3, 0 )\n' | parse)" 'G|1.2e+3'
check ac_steps "$(printf '.meas ac g find abs(V(out)):\n1 (1.2e+3, 0)\n2 (1.3e+3, -0.0e+0)\n' | parse)" 'G|1.2e+3 1.3e+3'
check reject_complex "$(printf '.meas ac g find V(out):\n(1.2e3, -1e-20)\n' | parse)" ''
check reject_nan "$(printf '.meas dc g avg V(out):\nNaN\n' | parse)" ''
check next_record "$(printf '.meas dc a avg V(a):\n1\n\n.meas dc b avg V(b):\n-2\n' | parse)" $'A|1\nB|-2'

printf 'A abs 0.125 1\n' > "$task_tmp/case.expect"
check inclusive_abs "$(printf 'A|1.125\n' | compare)" 'OK|A|1.125'
printf 'A abs 0 1\n' > "$task_tmp/case.expect"
check zero_tolerance "$(printf 'A|1\n' | compare)" 'OK|A|1'
printf 'A rel 0.125 -2\n' > "$task_tmp/case.expect"
check negative_relative "$(printf 'A|-2.25\n' | compare)" 'OK|A|-2.25'
printf 'A warn_abs 0.1 1 2 3\n' > "$task_tmp/case.expect"
check missing_steps_fatal "$(printf 'A|1 2\n' | compare)" 'FAIL|A|1 2|missing steps: got 2 expected at least 3'
printf 'A max 0 7m\n' > "$task_tmp/case.expect"
check hard_limit_pass "$(printf 'A|0.006\n' | compare)" 'OK|A|0.006'
# Deliberately broken expectation must produce FAIL (not merely WARN).
printf 'A max 0 5m\n' > "$task_tmp/case.expect"
check mutated_limit_fails "$(printf 'A|0.006\n' | compare)" 'FAIL|A|0.006|1'
printf 'A warn_abs 0.0001 2m\n' > "$task_tmp/case.expect"
check typical_mismatch_warns "$(printf 'A|0.003\n' | compare)" 'WARN|A|0.003|1'
printf 'A max 0 5m\n' > "$task_tmp/case.expect"
check unlisted_measurement "$(printf 'B|1\n' | compare)" 'SKIP|B|1'
printf '%d parser/checker tests passed; no simulator was invoked.\n' "$count"
