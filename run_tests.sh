#!/usr/bin/env bash
# =============================================================================
#  run_tests.sh - regressionnyj progon biblioteki modelej QSPICE
#
#  Po umolchaniju nahodit core-stendy <component>/tests/*_test.cir. Application
#  stendy zapuskajutsja javnym putem ili --all. Izvlekaet .meas cherez QPOST,
#  sravnivaet s fajlom ozhidanij *.expect
#  i pechataet svodku. Kod vozvrata: 0 - hard-trebovanija proshli (WARN dopustimy),
#  1 - est' FAIL, 2 - oshibka okruzhenija.
#
#  WARN_AS_FAIL=1 delaet WARN fatal'nymi dlja CI.
#
#  ZAPUSK iz Git Bash:
#      chmod +x run_tests.sh
#      ./run_tests.sh
#      ./run_tests.sh 74HCT244            # tol'ko odin komponent
#      ./run_tests.sh applications/S6061NS-40DF
#      ./run_tests.sh --all                # core + application tests
#      KEEP_RAW=1 ./run_tests.sh          # ne udaljat' .qraw
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
#  garantirovannye min/max i application requirements - cherez hard-rezhimy.
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

QSPICE="$QSPICE_DIR/QSPICE64.exe"
QPOST="$QSPICE_DIR/QPOST.exe"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[90m'; RST=$'\033[0m'

for exe in "$QSPICE" "$QPOST"; do
    [ -f "$exe" ] || { echo "${RED}NE NAJDEN: $exe${RST}"
        echo "${YEL}QSPICE_DIR=\"/c/...\" ./run_tests.sh${RST}"; exit 2; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 2

# --- razbor vyvoda QPOST --------------------------------------------------
# Format: stroka ".meas tran <imja> ...:" , dalee stroki "<nomer shaga> <znachenie>"
# (bez .step - prosto "<znachenie>").
read -r -d '' PARSE_AWK <<'AWK' || true
/^[[:space:]]*\.meas[[:space:]]/ {
    nm = $3; gsub(/[:,]$/, "", nm); cur = toupper(nm)
    if (!(cur in seen)) { order[++n] = cur; seen[cur] = 1 }
    next
}
cur != "" && $0 ~ /^[[:space:]]*[0-9]+[[:space:]]+[-+]?[0-9.]+([eE][-+]?[0-9]+)?[[:space:]]*$/ {
    vals[cur] = vals[cur] (vals[cur] == "" ? "" : " ") $2; next
}
cur != "" && $0 ~ /^[[:space:]]*[-+]?[0-9.]+([eE][-+]?[0-9]+)?[[:space:]]*$/ {
    vals[cur] = vals[cur] (vals[cur] == "" ? "" : " ") $1; next
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

    split($2, v, " ")
    bad = ""
    for (i = 1; i in v; i++) {
        m = v[i] + 0
        e = (nexp[nm] >= i) ? exp_v[nm, i] : exp_v[nm, nexp[nm]]
        ok = 0
        if      (md == "abs") ok = (m - e < tol[nm] && e - m < tol[nm])
        else if (md == "rel") ok = (m - e < tol[nm]*e && e - m < tol[nm]*e)
        else if (md == "max") ok = (m <= e * (1 + tol[nm]))
        else if (md == "min") ok = (m >= e * (1 - tol[nm]))
        else {
            printf "BADMODE|%s|%s|%s\n", nm, $2, mode[nm]
            next
        }
        if (!ok) bad = bad " " i
    }
    if (bad == "")          printf "OK|%s|%s\n", nm, $2
    else if (severity=="WARN") printf "WARN|%s|%s|%s\n", nm, $2, substr(bad, 2)
    else                     printf "FAIL|%s|%s|%s\n", nm, $2, substr(bad, 2)
}
AWK

if [ -z "$TARGET" ]; then
    # Po umolchaniju regression biblioteki: tol'ko komponenty verhnego urovnja.
    # applications/ ne dolzhny delat' universal'nuju biblioteku zavisimoj ot odnogo izdelija.
    mapfile -t BENCHES < <(find . -mindepth 3 -maxdepth 3 -type f -path "./*/tests/*_test.cir" | sort)
elif [ "$TARGET" = "--all" ]; then
    mapfile -t BENCHES < <(find . -type f -path "*/tests/*_test.cir" | sort)
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

for bench in "${BENCHES[@]}"; do
    dir="$(dirname "$bench")"; file="$(basename "$bench")"; name="${file%.cir}"
    echo; echo "--- $name"
    pushd "$dir" >/dev/null || continue

    "$QSPICE" -binary "$file" >/dev/null 2>&1
    if [ ! -f "$name.qraw" ]; then
        echo "    ${RED}simulacija upala, .qraw net${RST}"
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi
    "$QPOST" "$file" -o "$name.out" >/dev/null 2>&1
    if [ ! -s "$name.out" ]; then
        echo "    ${RED}QPOST ne sozdal .out${RST}"
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi

    parsed=$(awk "$PARSE_AWK" "$name.out")
    if [ -z "$parsed" ]; then
        echo "    ${YEL}ni odnogo .meas ne razobrano - proverte $name.out${RST}"
        TOTAL_FAIL=$((TOTAL_FAIL+1)); popd >/dev/null; continue
    fi

    if [ ! -f "$name.expect" ]; then
        echo "    ${RED}NET FAJLA $name.expect - sverjat' ne s chem, stend ne zachtjon${RST}"
        TOTAL_FAIL=$((TOTAL_FAIL+1))
        echo "$parsed" | while IFS='|' read -r nm vals; do
            printf "${DIM}    %-14s %s${RST}\n" "$nm" "$vals"
        done
        popd >/dev/null; continue
    fi

    while IFS='|' read -r st nm vals which; do
        case "$st" in
            OK)   printf "    %-14s ${GRN}PASS${RST}\n" "$nm"
                  TOTAL_PASS=$((TOTAL_PASS+1)) ;;
            WARN) printf "    %-14s ${YEL}WARN${RST} (ugly: %s)\n" "$nm" "$which"
                  TOTAL_WARN=$((TOTAL_WARN+1)) ;;
            FAIL) printf "    %-14s ${RED}FAIL${RST} (ugly: %s)\n" "$nm" "$which"
                  TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
            SKIP) printf "${DIM}    %-14s  net v .expect${RST}\n" "$nm"
                  TOTAL_SKIP=$((TOTAL_SKIP+1)) ;;
            BADMODE) printf "    %-14s ${RED}NEIZVESTNYJ REZHIM: %s${RST}\n" "$nm" "$which"
                     TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
        esac
        printf "${DIM}    %-14s %s${RST}\n" "" "$vals"
    done < <(echo "$parsed" | awk "$CHECK_AWK" "$name.expect" -)

    [ "$KEEP_RAW" = "1" ] || rm -f "$name.qraw"
    popd >/dev/null
done

echo
printf '=%.0s' {1..70}; echo
printf "PASS: ${GRN}%d${RST}  WARN: ${YEL}%d${RST}  FAIL: ${RED}%d${RST}" \
       "$TOTAL_PASS" "$TOTAL_WARN" "$TOTAL_FAIL"
[ "$TOTAL_SKIP" -eq 0 ] || printf "  SKIP: ${DIM}%d${RST}" "$TOTAL_SKIP"
echo

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
