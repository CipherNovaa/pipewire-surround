#!/usr/bin/env python3
"""Route audio to whichever headphones are actually in use, in binaural surround.

Three destinations, in priority order:

  1. a priority device (config "priority_sink_prefixes", e.g. a USB earbud
     dongle) when its sink is present            -- surround
  2. primary_sink, when the headset is powered on -- surround
  3. primary_sink, when it is not                 -- PLAIN STEREO (speakers)

(3) is stereo on purpose: binaural HRTF assumes the sound arrives at your eardrum,
so on real speakers your own head applies the ear filtering a second time and it
sounds hollow and phasey.

Why headset detection needs headsetcontrol: some wireless headsets have speakers
wired to the base station's line-out, so headphones and amp are the SAME ALSA
device and PipeWire cannot tell them apart. The only thing that knows is the base
station's HID interface. With headset_detect = "always" this step is skipped.
"""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import surround_config
CFG = surround_config.load()
HERE = os.path.dirname(os.path.realpath(__file__))

SURROUND = "effect_input.vsurround71"
PRIMARY  = CFG["primary_sink"]
PRIORITY = CFG["priority_sink_prefixes"]
TARGET   = f"{CFG['cache_dir']}/target"
# The primary sink can be ONE ALSA device for both the headset and speakers on its
# line-out, so it has one volume -- and the volume keys move the DEFAULT sink:
# the device itself in speaker mode, the surround filter in headset mode. Every
# speaker turn-down therefore stuck to the headset, and headset turn-ups only
# reached the filter (capped at 100%): the headset ratcheted quieter day by day
# (the hardware sat at 24%, -37 dB). Each mode now keeps its OWN level.
LEVELS   = f"{CFG['cache_dir']}/levels.json"
# headset (surround) mode: the hardware stays at full; loudness is the filter's
HEADSET_DEFAULT = CFG["headset_level"]
POLL     = 1
CONFIRM  = 2


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def sinks():
    out = []
    for line in sh("pactl", "list", "short", "sinks").splitlines():
        f = line.split("\t")
        if len(f) > 1:
            out.append(f[1])
    return out


def priority_sink():
    for s in sinks():
        if any(s.startswith(p) for p in PRIORITY):
            return s
    return None


def headset_on():
    """battery.status describes the BASE STATION (always plugged in, always reads
    AVAILABLE), so it tells us nothing. battery.level is the real discriminator:
    -1/UNAVAILABLE when off, >0 when awake, and a transient 0 during handoff which
    we report as None so it is ignored rather than acted on."""
    if CFG["headset_detect"] != "headsetcontrol":
        return True
    try:
        r = subprocess.run(["headsetcontrol", "-o", "json"],
                           capture_output=True, text=True, timeout=5)
        for dev in json.loads(r.stdout).get("devices", []):
            b = dev.get("battery", {})
            if "UNAVAILABLE" in str(b.get("status", "")).upper():
                return False
            lvl = b.get("level")
            if isinstance(lvl, (int, float)):
                return True if lvl > 0 else None
    except Exception:
        return None
    return None


def decide():
    """-> (hardware sink, use_surround) or None while the answer is unknowable."""
    ps = priority_sink()
    if ps:
        return (ps, True)
    on = headset_on()
    if on is None:
        return None
    return (PRIMARY, True) if on else (PRIMARY, False)


def retarget(hw):
    """Re-pin the filter chain's output. The target is baked into the generated
    config, so this regenerates and restarts it -- surround_profile.py parks any
    playing streams across the restart so audio doesn't drop."""
    try:
        cur = open(TARGET).read().strip()
    except Exception:
        cur = ""
    if cur == hw:
        return
    open(TARGET, "w").write(hw)
    prof_py = os.path.join(HERE, "surround_profile.py")
    prof = subprocess.run([prof_py, "current"],
                          capture_output=True, text=True).stdout.split()
    subprocess.run([prof_py, "set"] +
                   ([p for p in prof if p != "(none)"] or [CFG["default_profile"]]),
                   capture_output=True)
    print("target -> %s" % hw.split(".")[-2][:40], flush=True)


def hw_volume(sink):
    """The sink's volume in pactl percent (first channel), or None."""
    out = sh("pactl", "get-sink-volume", sink)
    for tok in out.split():
        if tok.endswith("%") and tok[:-1].isdigit():
            return int(tok[:-1])
    return None


def load_levels():
    try:
        return json.load(open(LEVELS))
    except Exception:
        return {}


def save_levels(levels):
    try:
        with open(LEVELS, "w") as f:
            json.dump(levels, f)
    except Exception:
        pass


def mode_key(choice):
    """Which remembered level a (sink, surround) choice uses — only the primary sink
    shares its device between modes."""
    hw, surround = choice
    if hw != PRIMARY:
        return None
    return "headset" if surround else "speakers"


def switch(sink):
    if sh("pactl", "get-default-sink") == sink:
        return
    subprocess.run(["pactl", "set-default-sink", sink], capture_output=True)
    for line in sh("pactl", "list", "short", "sink-inputs").splitlines():
        idx = line.split("\t")[0]
        subprocess.run(["pactl", "move-sink-input", idx, sink], capture_output=True)
    print("-> %s" % sink, flush=True)


def main():
    last, pending, count = None, None, 0
    levels = load_levels()
    while True:
        d = decide()
        # Track the current mode's hardware level (the keys, a mixer) so a switch
        # can bank it and the other mode restores its own.
        key = mode_key(last) if last else None
        if key:
            v = hw_volume(PRIMARY)
            if v is not None and levels.get(key) != v:
                levels[key] = v
                save_levels(levels)
        if d is not None:
            if d == pending:
                count += 1
            else:
                pending, count = d, 1
            if count >= CONFIRM and pending != last:
                hw, surround = pending
                new_key = mode_key(pending)
                if new_key:
                    want = levels.get(new_key, HEADSET_DEFAULT if new_key == "headset" else CFG["speaker_level"])
                    if want is not None and hw_volume(PRIMARY) != want:
                        subprocess.run(["pactl", "set-sink-volume", PRIMARY, "%d%%" % want],
                                       capture_output=True)
                        print("primary %s level -> %d%%" % (new_key, want), flush=True)
                # Retarget in BOTH branches. Doing it only for surround left the
                # filter chain pinned to hardware that had been unplugged (target
                # file still naming the PS Link adapter after the dongle was pulled),
                # so the chain sat connected to nothing until surround next engaged.
                retarget(hw)
                switch(SURROUND if surround else hw)
                last = pending
        time.sleep(POLL)


if __name__ == "__main__":
    if "--once" in sys.argv:
        d = decide()
        print("priority=%s headset_on=%s -> %s" % (bool(priority_sink()), headset_on(), d))
        sys.exit(0)
    main()
