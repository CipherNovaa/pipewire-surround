#!/usr/bin/env python3
"""Generate the PipeWire Virtual Surround 7.1 filter-chain for a given HRTF profile.

Two kinds of profile:
  sofa   -- a SOFA HRTF database; each of the 8 speaker channels is spatialised
            at its true angle by libmysofa. Physically principled, and the same
            database can do arbitrary angles.
  hesuvi -- a 14-channel HeSuVi impulse response captured from a commercial
            surround virtualiser (Dolby Atmos, DTS Headphone:X, Sennheiser GSX...).
            These bake in room reflections, which is why they sound "outside
            your head" more convincingly than a raw anechoic HRTF.

Usage:  surround_profile.py list | current | set <name>
"""
import os, re, sys, time, glob, json, subprocess, textwrap
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import surround_config
CFG = surround_config.load()

HOME  = os.path.expanduser("~")
HRTF  = CFG["hrtf_dir"]
CONF  = CFG["pipewire_conf"]
STAMP = f"{CFG['cache_dir']}/profile"

# 7.1 speaker angles. SOFA convention: 0 = ahead, degrees counter-clockwise.
ANGLES = {"FL": 30.0, "FR": 330.0, "FC": 0.0, "LFE": 0.0,
          "RL": 150.0, "RR": 210.0, "SL": 90.0, "SR": 270.0}
ORDER  = ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"]

# HeSuVi packs 7 speakers x 2 ears into 14 channels in this fixed order.
HESUVI_CH = {"FL": (0, 1), "SL": (2, 3), "RL": (4, 5), "FC": (6, 13),
             "FR": (8, 7), "SR": (10, 9), "RR": (12, 11), "LFE": (6, 13)}


def profiles():
    p = {}
    for f in sorted(glob.glob("/usr/share/libmysofa/*.sofa")):
        if os.path.islink(f):
            continue
        p[os.path.basename(f)[:-5].lower()] = ("sofa", f)
    for f in sorted(glob.glob(f"{HRTF}/**/*.sofa", recursive=True)):
        if "_48K_" in f or "_44K_" in f:
            p["ku100" if "/D1_" in f else os.path.basename(f)[:-5].lower()] = ("sofa", f)
    for f in sorted(glob.glob(f"{HRTF}/hesuvi/hrir/*.wav")):
        p[os.path.basename(f)[:-4]] = ("hesuvi", f)
    return p


def sofa_graph(path):
    nodes = "\n".join(
        f'                    {{ type = sofa label = spatializer name = sp{c}\n'
        f'                      config = {{ filename = "{path}" }}\n'
        f'                      control = {{ "Azimuth" = {ANGLES[c]} "Elevation" = 0.0 "Radius" = 1.2 }} }}'
        for c in ORDER)
    links = "\n".join(
        f'                    {{ output = "sp{c}:Out L" input = "mixL:In {i}" }}\n'
        f'                    {{ output = "sp{c}:Out R" input = "mixR:In {i}" }}'
        for i, c in enumerate(ORDER, 1))
    inputs = " ".join(f'"sp{c}:In"' for c in ORDER)
    return nodes, links, inputs


def hesuvi_graph(path):
    nodes, links, inputs = [], [], []
    for i, c in enumerate(ORDER, 1):
        l, r = HESUVI_CH[c]
        nodes.append(
            f'                    {{ type = builtin label = convolver name = cv{c}L config = {{ filename = "{path}" channel = {l} }} }}\n'
            f'                    {{ type = builtin label = convolver name = cv{c}R config = {{ filename = "{path}" channel = {r} }} }}\n'
            f'                    {{ type = builtin label = copy name = cp{c} }}')
        links.append(
            f'                    {{ output = "cp{c}:Out" input = "cv{c}L:In" }}\n'
            f'                    {{ output = "cp{c}:Out" input = "cv{c}R:In" }}\n'
            f'                    {{ output = "cv{c}L:Out" input = "mixL:In {i}" }}\n'
            f'                    {{ output = "cv{c}R:Out" input = "mixR:In {i}" }}')
        inputs.append(f'"cp{c}:In"')
    return "\n".join(nodes), "\n".join(links), " ".join(inputs)


def module(name, suffix, label):
    p = profiles()
    if name not in p:
        sys.exit(f"unknown profile '{name}'. try: surround_profile.py list")
    kind, path = p[name]
    nodes, links, inputs = (sofa_graph if kind == "sofa" else hesuvi_graph)(path)
    nodes = re.sub(r"\b(sp|cv|cp)([A-Z]+[LR]?)\b", rf"\1\2{suffix}", nodes)
    links = re.sub(r"\b(sp|cv|cp)([A-Z]+[LR]?)\b", rf"\1\2{suffix}", links)
    inputs = re.sub(r"\b(sp|cv|cp)([A-Z]+[LR]?)\b", rf"\1\2{suffix}", inputs)
    nodes = nodes.replace("mixL", f"mixL{suffix}").replace("mixR", f"mixR{suffix}")
    links = links.replace("mixL", f"mixL{suffix}").replace("mixR", f"mixR{suffix}")
    eq_nodes, eq_links, eq_out = eq_chain(suffix)
    mixgain = " ".join(f'"Gain {i}" = {HEADROOM:.4f}' for i in range(1, 9))
    return textwrap.dedent(f"""\
        # profile: {name}  ({kind})  ->  {label}
        # source : {path}
        {{ name = libpipewire-module-filter-chain
            flags = [ nofail ]
            args = {{
                node.description = "{label} [{name}]"
                media.name       = "{label} [{name}]"
                filter.graph = {{
                    nodes = [
    {nodes}
                        {{ type = builtin label = mixer name = mixL{suffix} control = {{ {mixgain} }} }}
                        {{ type = builtin label = mixer name = mixR{suffix} control = {{ {mixgain} }} }}
    {eq_nodes}
                    ]
                    links = [
    {links}
    {eq_links}
                    ]
                    inputs  = [ {inputs} ]
                    outputs = [ "{eq_out['L']}:Out" "{eq_out['R']}:Out" ]
                }}
                capture.props = {{
                    node.name      = "effect_input.vsurround71{suffix}"
                    media.class    = Audio/Sink
                    audio.channels = 8
                    audio.position = [ FL FR FC LFE RL RR SL SR ]
                    # stereo stays stereo instead of being faked into 7.1
                    channelmix.upmix = false
                }}
                playback.props = {{
                    node.name      = "effect_output.vsurround71{suffix}"
                    # node.target, NOT target.object -- target.object fails
                    # SILENTLY here and wireplumber falls back to whatever sink
                    # its policy likes (it picked an NVIDIA HDMI output, = silence).
                    node.target    = "{OUTPUT_TARGET}"
                    # ...and REFUSE to be re-routed. Without this, wireplumber
                    # moves this link the moment the surround sink becomes the
                    # default (it resolves "default" to this sink, sees a loop,
                    # and wanders off to whatever is next in its priority list --
                    # an NVIDIA HDMI output, i.e. silence).
                    node.dont-reconnect = true
                    audio.channels = 2
                    audio.position = [ FL FR ]
                }}
            }}
        }}
    """)


SINK = "effect_input.vsurround71"

# Where the binaural stereo goes. Pinned EXPLICITLY rather than following the
# default sink -- if it followed the default it would point at itself the moment
# the autoswitch made this sink the default, which is exactly the feedback loop
# that used to kill audio (EasyEffects had the same flaw and had to leave the path).
PRIMARY = CFG["primary_sink"]
TARGET_FILE = f"{CFG['cache_dir']}/target"


def output_target():
    """Which hardware the binaural stereo is pinned to. The autoswitch writes this
    when you move between the primary headphones and a priority device."""
    try:
        t = open(TARGET_FILE).read().strip()
        if t:
            return t
    except Exception:
        pass
    return PRIMARY


OUTPUT_TARGET = output_target()

# The 10 EQ bands that sit at the END of the chain, after the HRTF mixer. Their
# gains are read from the EQ state file (eq_state.json) so a profile switch never
# loses your EQ. Runtime changes come through surround_eq.py (no restart needed).
# Headroom. Summing 8 convolved channels adds real gain -- measured +13.6 dB with
# 7.1 content (stereo only sees +1.7 dB, since just FL/FR are active). Without this,
# loud game audio clips hard on the way into the 16-bit USB output. Applied at the
# mixer inputs so it costs one multiply, not another filter stage.
HEADROOM_DB = CFG["headroom_db"]
HEADROOM = 10 ** (HEADROOM_DB / 20.0)

EQ_FREQS = [32, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_STATE = CFG["eq_state"]


def eq_gains():
    try:
        d = json.load(open(EQ_STATE))
        if not d.get("enabled", True):
            return [0.0] * len(EQ_FREQS)
        return [float(d.get("b%d" % (i + 1), 0) or 0) for i in range(len(EQ_FREQS))]
    except Exception:
        return [0.0] * len(EQ_FREQS)


def eq_chain(suffix):
    """Peaking filters chained after the binaural mixer, one leg per ear."""
    nodes, links, out = [], [], {}
    gains = eq_gains()
    for ear in ("L", "R"):
        prev = f"mix{ear}{suffix}"
        for n, (f, g, q) in enumerate((fr, gains[i], 1.0) for i, fr in enumerate(EQ_FREQS)):
            nm = f"eq{ear}{n}{suffix}"
            nodes.append(f'                        {{ type = builtin label = bq_peaking name = {nm} '
                         f'control = {{ "Freq" = {f} "Q" = {q} "Gain" = {g} }} }}')
            links.append(f'                        {{ output = "{prev}:Out" input = "{nm}:In" }}')
            prev = nm
        out[ear] = prev
    return "\n".join(nodes), "\n".join(links), out


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def sink_id(name):
    for line in sh("pactl", "list", "short", "sinks").splitlines():
        f = line.split("\t")
        if len(f) > 1 and f[1] == name:
            return f[0]
    return None


def fallback():
    """Somewhere safe to park audio while the filter chain is down."""
    for line in sh("pactl", "list", "short", "sinks").splitlines():
        f = line.split("\t")
        if len(f) > 1 and not f[1].startswith("effect_"):
            return f[1]
    return None


def restart_gracefully():
    """Restarting filter-chain DESTROYS and recreates the sink. If it happens to
    be the default at the time, every playing stream is left pointing at a node
    that no longer exists and the audio simply stops -- which is exactly what
    bit us. So: park everything somewhere safe first, restart, then put it back."""
    old = sink_id(SINK)
    was_default = sh("pactl", "get-default-sink") == SINK
    safe = fallback()
    moved = []
    if old and safe:
        for line in sh("pactl", "list", "short", "sink-inputs").splitlines():
            f = line.split("\t")
            if len(f) > 1 and f[1] == old:
                moved.append(f[0])
                subprocess.run(["pactl", "move-sink-input", f[0], safe],
                               capture_output=True)
    if was_default and safe:
        subprocess.run(["pactl", "set-default-sink", safe], capture_output=True)

    subprocess.run(["systemctl", "--user", "restart", "filter-chain.service"], check=True)

    new = None
    for _ in range(40):                      # wait for the new sink, up to ~10s
        time.sleep(0.25)
        new = sink_id(SINK)
        if new and new != old:
            break
    if not new:
        print("WARNING: surround sink did not come back; audio left on", safe)
        return
    if was_default:
        subprocess.run(["pactl", "set-default-sink", SINK], capture_output=True)
        for i in moved:
            subprocess.run(["pactl", "move-sink-input", i, SINK], capture_output=True)


def write(names):
    """names[0] = main sink. names[1] (optional) = a second, simultaneous sink
    with a different HRTF -- e.g. a tight one for competitive games and a
    roomier one for films, both live, chosen per-app instead of toggled."""
    mods = [module(names[0], "", "Virtual Surround 7.1")]
    if len(names) > 1:
        mods.append(module(names[1], "b", "Virtual Surround 7.1 alt"))
    conf = ("# GENERATED by surround_profile.py -- edits will be overwritten.\n"
            "context.modules = [\n" + "\n".join(mods) + "\n]\n")
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    open(CONF, "w").write(conf)
    open(STAMP, "w").write(" ".join(names))
    restart_gracefully()
    print("profile -> " + " + ".join(names))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "current"
    if cmd == "list":
        for n, (k, _) in sorted(profiles().items(), key=lambda x: (x[1][0], x[0])):
            print(f"{k:7s} {n}")
    elif cmd == "current":
        print(open(STAMP).read().strip() if os.path.exists(STAMP) else "(none)")
    elif cmd == "set":
        write(sys.argv[2:])
    else:
        sys.exit(__doc__)
