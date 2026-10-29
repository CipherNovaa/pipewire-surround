"""Shared configuration for the surround toolkit.

Reads ~/.config/surround/config.json (override with $SURROUND_CONFIG). Every key
is optional; see examples/config.example.json for the full list.
"""
import json, os

HOME = os.path.expanduser("~")
CONFIG_PATH = os.environ.get("SURROUND_CONFIG", f"{HOME}/.config/surround/config.json")
HERE = os.path.dirname(os.path.realpath(__file__))
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

DEFAULTS = {
    # Hardware sink the binaural stereo is pinned to by default (pactl sink name).
    "primary_sink": "",
    # Sink-name PREFIXES that win when present (e.g. a USB dongle for earbuds).
    # Presence of one means: surround, routed to it.
    "priority_sink_prefixes": [],
    # How to tell whether the headset on primary_sink is powered on:
    #   "headsetcontrol" -- battery.level from `headsetcontrol -o json`
    #   "always"         -- primary_sink is always headphones (always surround)
    "headset_detect": "always",
    # primary_sink is also the speakers' output (same ALSA device): remember a
    # separate volume per mode. Percent; null = leave whatever it is.
    "headset_level": 100,
    "speaker_level": None,
    "hrtf_dir": f"{HOME}/.local/share/hrtf",
    "default_profile": "atmos",
    "pipewire_conf": f"{HOME}/.config/pipewire/filter-chain.conf.d/20-virtual-surround-71.conf",
    "eq_state": f"{RUNTIME}/surround/eq_state.json",
    "headroom_db": -6.0,
    "cache_dir": f"{HOME}/.cache/surround",
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    for k in ("hrtf_dir", "pipewire_conf", "eq_state", "cache_dir"):
        cfg[k] = os.path.expanduser(cfg[k])
    os.makedirs(cfg["cache_dir"], exist_ok=True)
    return cfg
