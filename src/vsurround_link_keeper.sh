#!/usr/bin/env bash
# Keeps the vsurround71 filter-chain output linked to its configured sink.
#
# Why this exists: the chain sets node.dont-reconnect = true so wireplumber
# can't wander the route off to an NVIDIA HDMI output (= silence). The cost
# is that when the target device disappears -- headset power-cycled, USB
# re-enumerated -- the stream is never re-attached when it comes back. The
# chain stays "active (running)" with its output linked to nothing, and the
# game plays into a void. Seen 2026-09-15: 2 days uptime, zero links out.
#
# Target and node name are read from the filter-chain conf so there is one
# source of truth.

set -uo pipefail

CONF="${VSURROUND_CONF:-$HOME/.config/pipewire/filter-chain.conf.d/20-virtual-surround-71.conf}"
POLL=30          # safety re-check even if no graph events arrive
DEBOUNCE=0.5     # collapse bursts of graph churn into one check

log() { printf '%s\n' "$*"; }

# Pull node.name / node.target out of the playback.props block only --
# node.name also appears in capture.props and would otherwise win.
read_conf() {
    awk '
        /playback\.props/          { inblk = 1 }
        inblk && /node\.name/      { gsub(/.*=[ \t]*"|".*/, ""); name = $0 }
        inblk && /node\.target/    { gsub(/.*=[ \t]*"|".*/, ""); target = $0 }
        inblk && /^[ \t]*}/        { if (name != "") exit }
        END                        { print name; print target }
    ' "$CONF"
}

ports_of() { pw-link "$1" 2>/dev/null | grep -E "^$(sed 's/[].[^$\\*/]/\\&/g' <<<"$1"):" ; }

ensure_linked() {
    local out_node="$1" target="$2"
    local -a out_ports target_ports

    mapfile -t out_ports   < <(pw-link -o 2>/dev/null | grep -F "${out_node}:")
    mapfile -t target_ports < <(pw-link -i 2>/dev/null | grep -F "${target}:playback_")

    # Chain not up yet: filter-chain.service and its own guard own that case.
    (( ${#out_ports[@]} )) || return 0
    # Device absent (headset off). Nothing to do, and nothing to complain about.
    (( ${#target_ports[@]} )) || return 0

    local existing
    existing=$(pw-link -l "$out_node" 2>/dev/null | grep -F "$target" | wc -l)
    (( existing >= ${#out_ports[@]} )) && return 0

    # Pair positionally, not by port-name suffix: the chain emits output_FL/FR
    # but sinks are not required to match (HDMI outputs use playback_AUX0/AUX1).
    local n=${#out_ports[@]}
    (( ${#target_ports[@]} < n )) && n=${#target_ports[@]}

    local linked=0 i
    for (( i = 0; i < n; i++ )); do
        pw-link "${out_ports[i]}" "${target_ports[i]}" 2>/dev/null && (( linked++ ))
    done

    (( linked )) && log "relinked $out_node -> $target ($linked/$n channels)"
    return 0
}

main() {
    local out_node target
    { read -r out_node; read -r target; } < <(read_conf)

    if [[ -z $out_node || -z $target ]]; then
        log "could not parse node.name / node.target from $CONF" >&2
        exit 1
    fi
    log "watching $out_node -> $target"

    local fd_open=0
    while :; do
        if (( ! fd_open )); then
            exec 3< <(pw-link -m -l 2>/dev/null) && fd_open=1 || { sleep 5; continue; }
        fi

        ensure_linked "$out_node" "$target"

        # Block until the graph changes or POLL elapses, then swallow the
        # burst that follows so one reconnect is one check, not fifty.
        if read -t "$POLL" -r _ <&3; then
            while read -t "$DEBOUNCE" -r _ <&3; do :; done
        elif (( $? <= 128 )); then
            # EOF, not timeout: pipewire went away. Reopen the monitor.
            exec 3<&- 2>/dev/null
            fd_open=0
            sleep 2
        fi
    done
}

main "$@"
