#!/usr/bin/env bash
# Rebuilds the board files of the sinowealth-g5kb family from the vendor apps' KB.ini files.
#
#   tools/make-g5kb-boards.sh <folder with the unpacked installers>
#
# The folder holds one sub-folder per installer, unpacked with innoextract (never run them):
#   innoextract -s -d <folder>/aula-f65 "AULA F65 driver.exe"
# The installers come from https://www.aulastar.com/keyboard/ (Driver links). The vendor files
# themselves stay out of this repo; only what facet-keyboard needs ends up in boards/.
# aula-f75 keeps its hand-drawn top row (the picture in the app is not to scale there).
set -euo pipefail

src=${1:?folder with the unpacked installers}
here=$(dirname "$(readlink -f "$0")")
out=$here/../keyboard/.local/share/facet/keyboard/boards
py=${PYTHON:-python3}

board() {
  local id=$1 vendor=$2 source=$3
  shift 3
  local inis=() opts=()
  while (($#)); do
    case $1 in
      --*) opts+=("$1" "$2"); shift 2 ;;
      *) inis+=("$src/$1"); shift ;;
    esac
  done
  "$py" -I "$here/kbini2board.py" --id "$id" --vendor "$vendor" --source "$source" "${opts[@]}" "${inis[@]}" >"$out/$id.json.new"
  mv "$out/$id.json.new" "$out/$id.json"
  echo "$id"
}

board aula-f65 AULA "AULA F65 driver.exe (aulastar.com), Dev/kb/KB.ini" \
  aula-f65/app/Dev/kb/KB.ini
board aula-f65-pro AULA "AULA F65PRO driver.exe (aulastar.com), Dev/kb/F65PRO/KB.ini" \
  aula-f65pro/app/Dev/kb/F65PRO/KB.ini --name "AULA F65 Pro"
f75_top='0=[["esc",1],["",0.5],["f1",1],["f2",1],["f3",1],["f4",1],["",0.5],["f5",1],["f6",1],["f7",1],["f8",1],["",0.5],["f9",1],["f10",1],["f11",1],["f12",1],["",0.5],["knob",1]]'
board aula-f75 AULA "AULA_F75_Setup_v2.0_20240509.zip and AULA F75 driver.exe (aulastar.com), Dev/kb/1/KB.ini, Dev/kb/wired/KB.ini" \
  aula-f75/app/Dev/kb/1/KB.ini aula-f75/app/Dev/kb/wired/KB.ini --row "$f75_top" --alias "F75 Wired" --notes docs/boards/aula-f75.md
board aula-f75-kr AULA "AULA F75 driver.exe (aulastar.com), Dev/kb/F75KR/KB.ini" \
  aula-f75/app/Dev/kb/F75KR/KB.ini --row "$f75_top" --notes docs/boards/aula-f75.md
# The F99 file in AULA F99 driver.exe is the German (ISO) edition; the drawn layout is the ANSI
# one from the F99 Pro package, the ISO key comes along in the key list
board aula-f99 AULA "AULA F99 Pro driver.exe (Dev/kb/1, Dev/kb/wired) and AULA F99 driver.exe (Dev/kb), aulastar.com" \
  aula-f99pro/app/Dev/kb/1/KB.ini aula-f99pro/app/Dev/kb/wired/KB.ini aula-f99/app/Dev/kb/KB.ini --alias "F99 Wired"
board aula-f99-pro AULA "AULA F99 Pro driver.exe (aulastar.com), Dev/kb/F99Pro/KB.ini" \
  aula-f99pro/app/Dev/kb/F99Pro/KB.ini
board aula-au75 AULA "AULA AU75 driver.exe (aulastar.com), Dev/kb/AU75/KB.ini" \
  aula-au75/app/Dev/kb/AU75/KB.ini --name "AULA AU75"
board aula-fire75 AULA "AULA FIRE-75 driver.exe (aulastar.com), Dev/kb/FIRE75/KB.ini, Dev/kb/FIRE75USB/KB.ini" \
  aula-fire75/app/Dev/kb/FIRE75/KB.ini aula-fire75/app/Dev/kb/FIRE75USB/KB.ini --name "AULA FIRE-75"
