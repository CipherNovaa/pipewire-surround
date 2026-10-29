#!/usr/bin/env bash
# surround.sh -- Virtual Surround 7.1 (HRTF binaural) control.
#
#   surround.sh test          walk a spoken voice around all 8 speaker positions
#   surround.sh profile       rofi picker: switch HRTF, hear the difference
#   surround.sh profile NAME  switch directly
#   surround.sh run CMD...    launch just CMD into the main surround sink
#   surround.sh alt CMD...    launch CMD into the SECOND sink (different HRTF)
#   surround.sh on / off      make it (or stop it being) the default output
#   surround.sh status        what's live right now
set -uo pipefail

SINK="effect_input.vsurround71"
STATE="${XDG_CACHE_HOME:-$HOME/.cache}/surround/prev-sink"
PROF="$(dirname "$(readlink -f "$0")")/surround_profile.py"
mkdir -p "$(dirname "$STATE")"

have_sink() { pactl list short sinks | grep -q "$SINK"; }
need_sink() { have_sink || { echo "surround sink missing; systemctl --user start filter-chain.service"; exit 1; }; }

# The ones actually worth your time, ahead of the other ~50.
notable() {
  cat <<'N'
atmos     Dolby Atmos for Headphones -- the popular all-rounder
dtshx     DTS Headphone:X -- wider, more diffuse
gsx       Sennheiser GSX -- tight and neutral, good for competitive
dh+       Dolby Headphone (larger room)
ku100     SADIE II Neumann KU100 -- anechoic, most accurate, driest
cmss_game Creative CMSS-3D, game tuning
razer     Razer Surround
waves     Waves Nx
default   MIT KEMAR -- the stock one you first heard
N
}

case "${1:-status}" in
  test)
    need_sink
    echo "Each position in turn. The voice should walk AROUND your head."
    t="$SINK"; [ "${2:-}" = "alt" ] && t="${SINK}b"
    PULSE_SINK="$t" speaker-test -D pulse -c 8 -t wav -l 1
    ;;
  profile)
    if [ $# -ge 2 ]; then "$PROF" set "$2"; exit $?; fi
    cur="$("$PROF" current)"
    sel=$( { notable | sed 's/^/* /'; echo "--- all ${#} ---" | sed 's/\${#}//'; \
             "$PROF" list | awk '{print $2"  ("$1")"}'; } \
      | rofi -dmenu -i -p "HRTF [now: $cur]" -l 18 ) || exit 0
    name=$(printf '%s' "$sel" | sed 's/^\* //' | awk '{print $1}')
    case "$name" in ""|"---") exit 0 ;; esac
    "$PROF" set "$name"
    ;;
  run)
    shift; [ $# -gt 0 ] || { echo "usage: surround.sh run <command> [args...]"; exit 1; }
    need_sink; exec env PULSE_SINK="$SINK" "$@"
    ;;
  alt)
    shift; [ $# -gt 0 ] || { echo "usage: surround.sh alt <command> [args...]"; exit 1; }
    pactl list short sinks | grep -q "${SINK}b" || { echo "no alt sink; set one: surround_profile.py set gsx dtshx"; exit 1; }
    exec env PULSE_SINK="${SINK}b" "$@"
    ;;
  on)
    need_sink; pactl get-default-sink > "$STATE"
    pactl set-default-sink "$SINK" && echo "default output -> Virtual Surround 7.1"
    ;;
  off)
    prev="$(cat "$STATE" 2>/dev/null)"
    [ -n "$prev" ] || prev="$(pactl list short sinks | grep -v effect_ | awk 'NR==1{print $2}')"
    pactl set-default-sink "$prev" && echo "default output -> $prev"
    ;;
  status)
    echo "filter-chain : $(systemctl --user is-active filter-chain.service)"
    echo "surround sink: $(have_sink && echo present || echo MISSING)"
    echo "HRTF profile : $("$PROF" current)"
    echo "alt sink     : $(pactl list short sinks | grep -q "${SINK}b" && echo present || echo none)"
    echo "default sink : $(pactl get-default-sink)"
    ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
