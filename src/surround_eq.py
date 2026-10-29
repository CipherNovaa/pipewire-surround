#!/usr/bin/env python3
"""Drive the Virtual Surround sink's built-in EQ from a state file.

Any UI (a shell widget, a script) writes eq_state.json; the 10 sliders map 1:1 onto 10 peaking filters that live at the END of
the binaural filter chain (after the HRTF mixer). This is built into the chain rather than using EasyEffects,
which had to leave the graph: as a service it always attached itself to the
default sink, so the moment the surround sink became the default it fed into
that sink while that sink fed back into it -- a loop, and silence.

Gains are pushed live via the filter-chain node's Props; no restart, no gap.
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import surround_config
CFG = surround_config.load()

STATE = CFG["eq_state"]
FREQS = [32, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
NODE  = "effect_input.vsurround71"


def gains():
    """Slider values b1..b10 in dB; all flat if the widget hasn't saved yet.

    When the widget's EQ toggle is off we push all-zero gains rather than tearing
    the filters out of the graph: a peaking filter at 0 dB is mathematically
    transparent, and this way toggling is instant and cannot drop audio."""
    try:
        d = json.load(open(STATE))
        if not d.get("enabled", True):
            return [0.0] * len(FREQS)
        return [float(d.get("b%d" % (i + 1), 0) or 0) for i in range(len(FREQS))]
    except Exception:
        return [0.0] * len(FREQS)


def node_id():
    out = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    for o in json.loads(out):
        if ((o.get("info") or {}).get("props") or {}).get("node.name") == NODE:
            return str(o["id"])
    return None


def apply():
    nid = node_id()
    if not nid:
        print("surround sink not present", file=sys.stderr)
        return 1
    parts = []
    for i, g in enumerate(gains()):
        parts += ['"eqL%d:Gain" %.2f' % (i, g), '"eqR%d:Gain" %.2f' % (i, g)]
    r = subprocess.run(["pw-cli", "set-param", nid, "Props",
                        "{ params = [ %s ] }" % " ".join(parts)],
                       capture_output=True, text=True)
    return r.returncode


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if cmd == "show":
        for f, g in zip(FREQS, gains()):
            print("%6d Hz  %+.1f dB" % (f, g))
    else:
        sys.exit(apply())
