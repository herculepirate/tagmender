#!/usr/bin/env bash
# tools/i18n_extract.sh - gather the app's words for translators (docs/TRANSLATING.md).
#
#   tools/i18n_extract.sh             assets/i18n/tagmender.ts (every tr() and N_() text in gui/ and
#                                     requirements.py, no translations: the template), and every
#                                     tagmender_<lang>.ts there
#                                     brought up to date (new texts added, translations kept)
#   tools/i18n_extract.sh --new de    also start assets/i18n/tagmender_de.ts for a new language
#   tools/i18n_extract.sh --release   then compile each tagmender_<lang>.ts to the .qm the app loads
#
# Needs pyside6-lupdate (and pyside6-lrelease for --release), which come with PySide6; set
# LUPDATE / LRELEASE to use other ones. Reads gui/, requirements.py and plugins.py; writes only under
# assets/i18n/.
set -euo pipefail
cd "$(dirname "$0")/.."

LUPDATE=${LUPDATE:-pyside6-lupdate}
LRELEASE=${LRELEASE:-pyside6-lrelease}
OUT=assets/i18n
new="" release=0
while [ $# -gt 0 ]; do
    case "$1" in
        --new) new=${2:?"--new needs a language code, such as de or pt_BR"}; shift 2 ;;
        --release) release=1; shift ;;
        -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$OUT"
mapfile -t sources < <(find gui -name '*.py' -not -path '*/__pycache__/*' | sort)
# the Setup page's items and notices, shown through tr()
[ -f requirements.py ] && sources+=(requirements.py)
[ -f plugins.py ] && sources+=(plugins.py)                   # (the built-in plugins' descriptions)
# tr("text") is Qt's tr() in the context "" (gui/i18n.py); N_("text") only marks a table's text
args=(-tr-function-alias "QT_TR_NOOP+=N_" -locations relative -source-language en)

"$LUPDATE" "${args[@]}" -no-obsolete "${sources[@]}" -ts "$OUT/tagmender.ts"

if [ -n "$new" ] && [ ! -e "$OUT/tagmender_$new.ts" ]; then
    "$LUPDATE" "${args[@]}" -target-language "$new" "${sources[@]}" -ts "$OUT/tagmender_$new.ts"
fi
for ts in "$OUT"/tagmender_*.ts; do
    [ -e "$ts" ] || continue
    lang=$(basename "$ts" .ts); lang=${lang#tagmender_}
    "$LUPDATE" "${args[@]}" -target-language "$lang" "${sources[@]}" -ts "$ts"
done

if [ "$release" = 1 ]; then
    for ts in "$OUT"/tagmender_*.ts; do
        [ -e "$ts" ] || continue
        "$LRELEASE" "$ts" -qm "${ts%.ts}.qm"
    done
fi
