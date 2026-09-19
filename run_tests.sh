#!/usr/bin/env bash
# =============================================================================
#  run_tests.sh - regressionnyj progon biblioteki modelej QSPICE
#
#  Nahodit universal'nye stendy <component>/tests/*_test.cir, izvlekaet .meas
#  cherez QPOST, sravnivaet s *.expect i sozdaet TEST_RESULTS.md.
#  Kod vozvrata: 0 - hard-trebovanija proshli (WARN dopustimy),
#  1 - est' FAIL, 2 - oshibka okruzhenija.
#
#  WARN_AS_FAIL=1 delaet WARN fatal'nymi dlja CI.
#
#  ZAPUSK iz Git Bash:
#      chmod +x run_tests.sh
#      ./run_tests.sh
#      ./run_tests.sh 74HCT244            # tol'ko odin komponent
#      ./run_tests.sh --all               # to zhe, chto bez argumenta
#      KEEP_RAW=1 ./run_tests.sh          # ne udaljat' .qraw
#      TEST_TIMEOUT_SEC=180 ./run_tests.sh # limit na QSPICE i QPOST
#      QSPICE_DIR="/c/Qspice" ./run_tests.sh
#
#  RJADOM S KAZHDYM STENDOM <imja>_test.cir dolzhen lezhat' <imja>_test.expect:
#
#      # izmerenie  rezhim  dopusk  ugol1 ugol2 ugol3 ugol4
#      A1_VOH       abs     0.03    4.20  3.98  3.84  3.70
#      A3_TPLH      max     0.15    14n   20n   25n   30n
#
#  hard-rezhimy:
#           abs |izm-ozh| <= dopusk
#           rel |izm-ozh| <= dopusk*ozh
#           max  izm <= ozh*(1+dopusk)     dlja predel'nyh znachenij dashita
#           min  izm >= ozh*(1-dopusk)
#  warning-rezhimy (ne valjat CI):
#           warn_abs | warn_rel | warn_max | warn_min
#  Tipichnye/nominal'nye tochki dashita sleduet proverjat' cherez WARN,
#  garantirovannye min/max i universal'nye funkcional'nye invarianty - hard.
#  Suffiksy p n u m k ponimajutsja. Uglov mozhet byt' men'she, chem shagov
#  .step - togda poslednee znachenie ispol'zuetsja dlja ostavshihsja.
#
#  POCHEMU VERDIKTY SCHITAJUTSJA ZDES', A NE V .meas PARAM
#  V QSPICE stroka vida
#      .meas TRAN PASS_A1 PARAM {if(abs(A1_VOH-VOH_EXP)<0.03,1,0)}
#  RABOTAET NEVERNO pri .step: sama shema stepaetsja pravil'no i A1_VOH
#  menjaetsja po uglam, no parametr VOH_EXP v vyrazhenii .meas zamorozhen
#  na znachenii POSLEDNEGO shaga. Proverено na real'nom prognone:
#  pri sovpadenii vseh chetyrjoh izmerenij s dashitom verdikt vydal
#  0 0 0 1 - "proshjol" tol'ko tot ugol, chej ozhidaemoe znachenie sluchajno
#  sovpalo s zamorozhennym. Poetomu sravnenie vynesено v etot skript.
# =============================================================================

set -u

QSPICE_DIR="${QSPICE_DIR:-/c/Program Files/QSPICE}"
TARGET="${1:-}"
KEEP_RAW="${KEEP_RAW:-0}"
WARN_AS_FAIL="${WARN_AS_FAIL:-0}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-180}"

QSPICE="$QSPICE_DIR/QSPICE64.exe"
QPOST="$QSPICE_DIR/QPOST.exe"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[90m'; RST=$'\033[0m'

for exe in "$QSPICE" "$QPOST"; do
    [ -f "$exe" ] || { echo "${RED}NE NAJDEN: $exe${RST}"
        echo "${YEL}QSPICE_DIR=\"/c/...\" ./run_tests.sh${RST}"; exit 2; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 2

case "$TEST_TIMEOUT_SEC" in
    ''|*[!0-9]*)
        echo "${RED}TEST_TIMEOUT_SEC dolzhen byt' celym chislom sekund${RST}"
        exit 2
        ;;
esac

run_limited() {
    if [ "$TEST_TIMEOUT_SEC" -gt 0 ] && command -v timeout >/dev/null 2>&1; then
        timeout "${TEST_TIMEOUT_SEC}s" "$@"
    else
        "$@"
    fi
}

# --- razbor vyvoda QPOST --------------------------------------------------
# Format: stroka ".meas tran <imja> ...:" , dalee stroki "<nomer shaga> <znachenie>"
# (bez .step - prosto "<znachenie>").
read -r -d '' PARSE_AWK <<'AWK' || true
function number(s) {
    return s ~ /^[-+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?$/
}
function append_value(v) {
    vals[cur] = vals[cur] (vals[cur] == "" ? "" : " ") v
}
/^[[:space:]]*\.meas[[:space:]]/ {
    nm = $3; gsub(/[:,]$/, "", nm); cur = toupper(nm)
    header = tolower($0)
    is_find_at[cur] = (header ~ /[[:space:]]find[[:space:]]/ && header ~ /[[:space:]]at[[:space:]]*=/)
    if (!(cur in seen)) { order[++n] = cur; seen[cur] = 1 }
    next
}
# QPOST may print real-valued AC expressions as (real, 0).
# Never silently discard a nonzero imaginary part: a complex measurement is
# not a scalar expectation and must remain missing / FAIL downstream.
cur != "" && $0 ~ /^[[:space:]]*([0-9]+[[:space:]]+)?\(/ {
    line=$0; sub(/^[[:space:]]+/, "", line)
    sub(/^[0-9]+[[:space:]]+/, "", line)
    if (line ~ /^\([^()]+,[^()]+\)[[:space:]]*$/) {
        sub(/^\(/, "", line); sub(/\)[[:space:]]*$/, "", line)
        split(line, pair, /,/)
        gsub(/[[:space:]]/, "", pair[1]); gsub(/[[:space:]]/, "", pair[2])
        if (number(pair[1]) && number(pair[2]) && pair[2]+0 == 0) {
            vals[cur] = vals[cur] (vals[cur] == "" ? "" : " ") pair[1]
            next
        }
    }
    cur=""; next
}
# A DC FIND ... AT= record contains the result and the sweep coordinate:
#     0.5005  -0.1
# With .step QPOST prefixes the row with the step number.  This must be
# handled before the generic two-column stepped-result rule below.
cur != "" && is_find_at[cur] {
    line=$0; sub(/^[[:space:]]+/, "", line)
    nf=split(line, field, /[[:space:]]+/)
    if (nf == 2 && number(field[1]) && number(field[2])) {
        append_value(field[1]); next
    }
    if (nf == 3 && field[1] ~ /^[0-9]+$/ && number(field[2]) && number(field[3])) {
        append_value(field[2]); next
    }
}
cur != "" && $0 ~ /^[[:space:]]*[0-9]+[[:space:]]+[-+]?[0-9.]+([eE][-+]?[0-9]+)?[[:space:]]*$/ {
    vals[cur] = vals[cur] (vals[cur] == "" ? "" : " ") $2; next
}
cur != "" && $0 ~ /^[[:space:]]*[-+]?[0-9.]+([eE][-+]?[0-9]+)?[[:space:]]*$/ {
    append_value($1); next
}
# QPOST annotates TRAN and DC MIN/MAX values with their independent variable:
#     0.250143 (at Time=1.58028e-06)
#     1.27676e-14 (at V_DIFF=0.016)
# Only the first scalar is the .meas result.  The annotation is metadata and
# must not make an otherwise valid measurement disappear.
cur != "" {
    line=$0; sub(/^[[:space:]]+/, "", line)
    split(line, field, /[[:space:]]+/)
    if (number(field[1]) && field[2] == "(at" && field[3] ~ /=/) {
        append_value(field[1]); next
    }
}
/^[[:space:]]*$/ { next }
{ cur = "" }
END { for (i=1;i<=n;i++) if (vals[order[i]] != "") printf "%s|%s\n", order[i], vals[order[i]] }
AWK

# --- sverka s ozhidanijami -------------------------------------------------
read -r -d '' CHECK_AWK <<'AWK' || true
function tonum(s,   m,u) {
    if (s ~ /[a-zA-Z]$/) {
        u = tolower(substr(s, length(s))); m = substr(s, 1, length(s)-1) + 0
        if (u=="p") return m*1e-12
        if (u=="n") return m*1e-9
        if (u=="u") return m*1e-6
        if (u=="m") return m*1e-3
        if (u=="k") return m*1e3
        return m
    }
    return s + 0
}
BEGIN { FS="[|]" }
NR==FNR {
    if ($0 ~ /^[[:space:]]*#/ || $0 ~ /^[[:space:]]*$/) next
    split($0, f, /[[:space:]]+/)
    nm = toupper(f[1]); mode[nm] = f[2]; tol[nm] = f[3] + 0
    ne = 0
    for (i = 4; i in f; i++) if (f[i] != "") exp_v[nm, ++ne] = tonum(f[i])
    nexp[nm] = ne
    next
}
{
    nm = $1
    if (!(nm in mode)) { printf "SKIP|%s|%s\n", nm, $2; next }

    md = mode[nm]
    severity = "FAIL"
    if (substr(md, 1, 5) == "warn_") {
        severity = "WARN"
        md = substr(md, 6)
    }

    nv = split($2, v, " ")
    # Missing data is a test-harness failure even for a typical/WARN target.
    if (nv < nexp[nm]) {
        printf "FAIL|%s|%s|missing steps: got %d expected at least %d\n", nm, $2, nv, nexp[nm]
        next
    }
    bad = ""
    for (i = 1; i <= nv; i++) {
        m = v[i] + 0
        e = (nexp[nm] >= i) ? exp_v[nm, i] : exp_v[nm, nexp[nm]]
        ok = 0
        if      (md == "abs") ok = (m - e <= tol[nm] && e - m <= tol[nm])
        else if (md == "rel") ok = (m - e <= tol[nm]*((e<0)?-e:e) && e - m <= tol[nm]*((e<0)?-e:e))
        else if (md == "max") ok = (m <= e * (1 + tol[nm]))
        else if (md == "min") ok = (m >= e * (1 - tol[nm]))
        else {
            printf "BADMODE|%s|%s|%s\n", nm, $2, mode[nm]
            next
        }
        if (!ok) bad = bad " " i
    }
    # An explicit expectation list also defines the minimum required number
    # of QPOST steps.  Extra measured steps still reuse the last expectation.
    for (i = nv + 1; i <= nexp[nm]; i++) bad = bad " " i
    if (bad == "")          printf "OK|%s|%s\n", nm, $2
    else if (severity=="WARN") printf "WARN|%s|%s|%s\n", nm, $2, substr(bad, 2)
    else                     printf "FAIL|%s|%s|%s\n", nm, $2, substr(bad, 2)
}
AWK

if [ -z "$TARGET" ] || [ "$TARGET" = "--all" ]; then
    # Biblioteka soderzhit tol'ko universal'nye komponentnye stendy.
    mapfile -t BENCHES < <(find . -mindepth 3 -maxdepth 3 -type f -path "./*/tests/*_test.cir" | sort)
elif [ -d "$TARGET" ]; then
    mapfile -t BENCHES < <(find "$TARGET" -type f -path "*/tests/*_test.cir" | sort)
elif [ -d "./$TARGET" ]; then
    mapfile -t BENCHES < <(find "./$TARGET" -type f -path "*/tests/*_test.cir" | sort)
else
    echo "${YEL}Ne najden katalog: $TARGET${RST}"
    exit 2
fi
[ "${#BENCHES[@]}" -gt 0 ] || { echo "${YEL}Stendy ne najdeny${RST}"; exit 2; }

echo
echo "Najdeno stendov: ${#BENCHES[@]}"
printf '=%.0s' {1..70}; echo

TOTAL_PASS=0
TOTAL_WARN=0
TOTAL_FAIL=0
TOTAL_SKIP=0

REPORT="$ROOT/TEST_RESULTS.md"
REPORT_TMP="$ROOT/.TEST_RESULTS.md.tmp"
RUN_SCOPE="${TARGET:-all components}"
trap 'rm -f "$REPORT_TMP"' EXIT

{
    echo "# QSPICElib - test results"
    echo
    echo "> Generated by \`./run_tests.sh\` on $(date -u '+%Y-%m-%d %H:%M:%S UTC')."
    echo
    echo "- Scope: \`$RUN_SCOPE\`"
    echo "- Benches: ${#BENCHES[@]}"
    echo "- Timeout per QSPICE/QPOST process: ${TEST_TIMEOUT_SEC} s (0 disables it)"
    echo
    echo "Values are reported in QSPICE SI units.  PASS checks guaranteed limits or"
    echo "universal functional invariants; WARN marks nominal fit or diagnostic limits."
} > "$REPORT_TMP"

for bench in "${BENCHES[@]}"; do
    dir="$(dirname "$bench")"; file="$(basename "$bench")"; name="${file%.cir}"
    component="${bench#./}"; component="${component%%/*}"
    echo; echo "--- $name"
    pushd "$dir" >/dev/null || continue

    {
        echo
        echo "## \`$component\` / \`$name\`"
        echo
        echo "| Measurement | Verdict | Values / details |"
        echo "|---|---|---|"
    } >> "$REPORT_TMP"

    # Nikogda ne prinimat' artefakty ot predydushhego/prervannogo zapuska.
    rm -f "$name.qraw" "$name.out"

    sim_rc=0
    run_limited "$QSPICE" -binary "$file" >/dev/null 2>&1 || sim_rc=$?
    if [ "$sim_rc" -ne 0 ]; then
        if [ "$sim_rc" -eq 124 ]; then
            echo "    ${RED}QSPICE timeout (${TEST_TIMEOUT_SEC} s)${RST}"
            printf '| `SIMULATION` | **FAIL** | timeout after %s s |\n' "$TEST_TIMEOUT_SEC" >> "$REPORT_TMP"
        else
            echo "    ${RED}QSPICE zavershilsja s kodom $sim_rc${RST}"
            printf '| `SIMULATION` | **FAIL** | QSPICE exit code %s |\n' "$sim_rc" >> "$REPORT_TMP"
        fi
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi
    if [ ! -f "$name.qraw" ]; then
        echo "    ${RED}simulacija upala, .qraw net${RST}"
        echo '| `SIMULATION` | **FAIL** | QSPICE did not create `.qraw` |' >> "$REPORT_TMP"
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi

    post_rc=0
    run_limited "$QPOST" "$file" -o "$name.out" >/dev/null 2>&1 || post_rc=$?
    if [ "$post_rc" -ne 0 ]; then
        if [ "$post_rc" -eq 124 ]; then
            echo "    ${RED}QPOST timeout (${TEST_TIMEOUT_SEC} s)${RST}"
            printf '| `QPOST` | **FAIL** | timeout after %s s |\n' "$TEST_TIMEOUT_SEC" >> "$REPORT_TMP"
        else
            echo "    ${RED}QPOST zavershilsja s kodom $post_rc${RST}"
            printf '| `QPOST` | **FAIL** | QPOST exit code %s |\n' "$post_rc" >> "$REPORT_TMP"
        fi
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi
    if [ ! -s "$name.out" ]; then
        echo "    ${RED}QPOST ne sozdal .out${RST}"
        echo '| `QPOST` | **FAIL** | empty or missing `.out` |' >> "$REPORT_TMP"
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi

    parsed=$(awk "$PARSE_AWK" "$name.out")
    if [ -z "$parsed" ]; then
        echo "    ${YEL}ni odnogo .meas ne razobrano - proverte $name.out${RST}"
        echo '| `QPOST` | **FAIL** | no `.meas` values parsed |' >> "$REPORT_TMP"
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi

    if [ ! -f "$name.expect" ]; then
        echo "    ${RED}NET FAJLA $name.expect - sverjat' ne s chem, stend ne zachtjon${RST}"
        TOTAL_FAIL=$((TOTAL_FAIL+1))
        printf '%s\n' "$parsed" | while IFS='|' read -r nm vals; do
            printf "${DIM}    %-14s %s${RST}\n" "$nm" "$vals"
            printf '| `%s` | **SKIP** | `%s`; no `.expect` |\n' "$nm" "$vals" >> "$REPORT_TMP"
        done
        popd >/dev/null; continue
    fi

    missing_expected="$(awk -F'|' '
        NR==FNR {
            if ($0 !~ /^[[:space:]]*#/ && $0 !~ /^[[:space:]]*$/) {
                line=$0; sub(/^[[:space:]]+/, "", line)
                split(line, fields, /[[:space:]]+/)
                nm=toupper(fields[1]); order[++n]=nm
            }
            next
        }
        { seen[$1]=1 }
        END { for (i=1;i<=n;i++) if (!(order[i] in seen)) print order[i] }
    ' "$name.expect" <(printf '%s\n' "$parsed"))"

    if [ -n "$missing_expected" ]; then
        while IFS= read -r nm; do
            echo "    ${RED}$nm NET V QPOST${RST}"
            printf '| `%s` | **FAIL** | expected measurement missing from QPOST |\n' "$nm" >> "$REPORT_TMP"
            TOTAL_FAIL=$((TOTAL_FAIL+1))
        done <<< "$missing_expected"
    fi

    checked="$(printf '%s\n' "$parsed" | awk "$CHECK_AWK" "$name.expect" -)"
    while IFS='|' read -r st nm vals which; do
        detail="$vals"
        [ -z "${which:-}" ] || detail="$detail; corners: $which"
        case "$st" in
            OK)   printf "    %-14s ${GRN}PASS${RST}\n" "$nm"
                  TOTAL_PASS=$((TOTAL_PASS+1)); verdict="PASS" ;;
            WARN) printf "    %-14s ${YEL}WARN${RST} (corners: %s)\n" "$nm" "$which"
                  TOTAL_WARN=$((TOTAL_WARN+1)); verdict="WARN" ;;
            FAIL) printf "    %-14s ${RED}FAIL${RST} (corners: %s)\n" "$nm" "$which"
                  TOTAL_FAIL=$((TOTAL_FAIL+1)); verdict="FAIL" ;;
            SKIP) printf "${DIM}    %-14s  net v .expect${RST}\n" "$nm"
                  TOTAL_SKIP=$((TOTAL_SKIP+1)); verdict="SKIP" ;;
            BADMODE) printf "    %-14s ${RED}NEIZVESTNYJ REZHIM: %s${RST}\n" "$nm" "$which"
                     TOTAL_FAIL=$((TOTAL_FAIL+1)); verdict="FAIL" ;;
        esac
        printf "${DIM}    %-14s %s${RST}\n" "" "$vals"
        printf '| `%s` | **%s** | `%s` |\n' "$nm" "$verdict" "$detail" >> "$REPORT_TMP"
    done <<< "$checked"

    [ "$KEEP_RAW" = "1" ] || rm -f "$name.qraw"
    popd >/dev/null
done

echo

{
    echo
    echo "## Summary"
    echo
    echo "| PASS | WARN | FAIL | SKIP |"
    echo "|---:|---:|---:|---:|"
    echo "| $TOTAL_PASS | $TOTAL_WARN | $TOTAL_FAIL | $TOTAL_SKIP |"
    echo
    if [ "$TOTAL_FAIL" -gt 0 ]; then
        echo "Overall result: **FAIL**"
    elif [ "$TOTAL_WARN" -gt 0 ]; then
        echo "Overall result: **PASS with WARN**"
    else
        echo "Overall result: **PASS**"
    fi
    echo
    echo "Detailed intent, datasheet sources, corner maps, and model limitations are"
    echo "documented in each component's \`tests/TEST_README.md\`."
} >> "$REPORT_TMP"

if ! mv -f "$REPORT_TMP" "$REPORT"; then
    echo "${RED}Ne udalos' zapisat' TEST_RESULTS.md${RST}"
    exit 2
fi
trap - EXIT

printf '=%.0s' {1..70}; echo
printf "PASS: ${GRN}%d${RST}  WARN: ${YEL}%d${RST}  FAIL: ${RED}%d${RST}" \
       "$TOTAL_PASS" "$TOTAL_WARN" "$TOTAL_FAIL"
[ "$TOTAL_SKIP" -eq 0 ] || printf "  SKIP: ${DIM}%d${RST}" "$TOTAL_SKIP"
echo
echo "Otchet: TEST_RESULTS.md"

if [ "$TOTAL_FAIL" -gt 0 ]; then
    echo "${RED}EST' HARD FAIL: $TOTAL_FAIL${RST}"
    exit 1
fi
if [ "$TOTAL_WARN" -gt 0 ]; then
    if [ "$WARN_AS_FAIL" = "1" ]; then
        echo "${RED}WARN_AS_FAIL=1: WARN SCHITAJUTSJA KAK FAIL; WARN=$TOTAL_WARN${RST}"
        exit 1
    fi
    echo "${YEL}VSE HARD-TREBOVANIJA PROSHLI; WARN: $TOTAL_WARN${RST}"
    exit 0
fi
echo "${GRN}VSE STENDY PROSHLI BEZ PREDUPREZHDENIJ${RST}"
exit 0
