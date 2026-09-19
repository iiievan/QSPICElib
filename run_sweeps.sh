#!/usr/bin/env bash
# =============================================================================
# run_sweeps.sh - universal characterization sweeps for QSPICElib
#
# Finds */characterization/*_sweep.cir, runs QSPICE + QPOST and writes:
#   <name>.out  - raw QPOST measurement report
#   <name>.csv  - one row per .step value, one column per .meas
#
# Sweeps are CHARACTERIZATION, not regression. No PASS/FAIL is assigned here.
# A sweep may explore a broad parameter space; project-specific acceptance
# belongs in applications/<device>/tests/*.expect.
#
# Usage from Git Bash:
#   ./run_sweeps.sh
#   ./run_sweeps.sh FDS4559
#   KEEP_RAW=1 ./run_sweeps.sh FDS4559
#   QSPICE_DIR="/c/Qspice" ./run_sweeps.sh FDS4559
#
# Current CSV helper expects one '.step param NAME list ...' per sweep bench.
# =============================================================================

set -u

QSPICE_DIR="${QSPICE_DIR:-/c/Program Files/QSPICE}"
TARGET="${1:-}"
KEEP_RAW="${KEEP_RAW:-0}"
QSPICE="$QSPICE_DIR/QSPICE64.exe"
QPOST="$QSPICE_DIR/QPOST.exe"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[90m'; RST=$'\033[0m'

for exe in "$QSPICE" "$QPOST"; do
    [ -f "$exe" ] || { echo "${RED}NE NAJDEN: $exe${RST}"; exit 2; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 2

if [ -z "$TARGET" ]; then
    mapfile -t SWEEPS < <(find . -mindepth 3 -maxdepth 3 -type f -path "./*/characterization/*_sweep.cir" | sort)
elif [ -d "$TARGET" ]; then
    mapfile -t SWEEPS < <(find "$TARGET" -type f -path "*/characterization/*_sweep.cir" | sort)
elif [ -d "./$TARGET" ]; then
    mapfile -t SWEEPS < <(find "./$TARGET" -type f -path "*/characterization/*_sweep.cir" | sort)
else
    echo "${YEL}Ne najden katalog: $TARGET${RST}"
    exit 2
fi

[ "${#SWEEPS[@]}" -gt 0 ] || { echo "${YEL}Sweep-stendy ne najdeny${RST}"; exit 2; }

echo
echo "Najdeno sweep-stendov: ${#SWEEPS[@]}"
printf '=%.0s' {1..70}; echo

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

TOTAL=0
for bench in "${SWEEPS[@]}"; do
    dir="$(dirname "$bench")"; file="$(basename "$bench")"; name="${file%.cir}"
    echo; echo "--- $name"
    pushd "$dir" >/dev/null || continue

    # Never accept stale artifacts from an interrupted previous run.
    rm -f "$name.qraw" "$name.out" "$name.csv"

    echo "    simulating..."
    "$QSPICE" -binary "$file" >/dev/null 2>&1
    if [ ! -f "$name.qraw" ]; then
        echo "    ${RED}simulacija upala, .qraw net${RST}"
        popd >/dev/null; continue
    fi
    qraw_size="$(du -h "$name.qraw" 2>/dev/null | awk '{print $1}')"
    echo "    qraw: ${qraw_size:-?}; QPOST..."
    "$QPOST" "$file" -o "$name.out" >/dev/null 2>&1
    if [ ! -s "$name.out" ]; then
        echo "    ${RED}QPOST ne sozdal .out${RST}"
        popd >/dev/null; continue
    fi

    parsed="$(awk "$PARSE_AWK" "$name.out")"
    if [ -z "$parsed" ]; then
        echo "    ${YEL}ni odnogo .meas ne razobrano${RST}"
        popd >/dev/null; continue
    fi

    step_line="$(grep -Ei '^[[:space:]]*\.step[[:space:]]+param[[:space:]]+[A-Za-z_][A-Za-z0-9_]*[[:space:]]+list[[:space:]]+' "$file" | head -n1 || true)"
    if [ -z "$step_line" ]; then
        echo "    ${YEL}CSV ne sozdan: nuzhen odin .step param NAME list ...${RST}"
    else
        read -ra sf <<< "$step_line"
        STEP_NAME="${sf[2]}"
        STEP_VALUES=("${sf[@]:4}")

        declare -a MEAS_ORDER=()
        declare -A DATA=()
        NSTEPS=0
        while IFS='|' read -r nm vals; do
            MEAS_ORDER+=("$nm")
            read -ra va <<< "$vals"
            [ "${#va[@]}" -gt "$NSTEPS" ] && NSTEPS="${#va[@]}"
            for ((i=0;i<${#va[@]};i++)); do DATA["$((i+1)),$nm"]="${va[$i]}"; done
        done <<< "$parsed"

        {
            printf 'step,%s' "$STEP_NAME"
            for nm in "${MEAS_ORDER[@]}"; do printf ',%s' "$nm"; done
            echo
            for ((i=1;i<=NSTEPS;i++)); do
                sv="${STEP_VALUES[$((i-1))]:-}"
                printf '%d,%s' "$i" "$sv"
                for nm in "${MEAS_ORDER[@]}"; do printf ',%s' "${DATA["$i,$nm"]:-}"; done
                echo
            done
        } > "$name.csv"
        unset DATA MEAS_ORDER
        echo "    ${GRN}CSV:${RST} $dir/$name.csv"
        echo "    steps=$NSTEPS, measurements=$(($(head -n1 "$name.csv" | awk -F, '{print NF}')-2))"
    fi

    if [ "$KEEP_RAW" = "1" ]; then
        echo "    qraw kept (KEEP_RAW=1)"
    else
        rm -f "$name.qraw"
        echo "    qraw removed (set KEEP_RAW=1 to keep waveforms)"
    fi
    TOTAL=$((TOTAL+1))
    popd >/dev/null
done

echo
printf '=%.0s' {1..70}; echo
echo "Characterization sweeps completed: $TOTAL"
echo "No PASS/FAIL: interpret CSV together with characterization/README.md"
