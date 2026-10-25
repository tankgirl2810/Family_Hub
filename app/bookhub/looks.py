"""The look of the whole hub: the Minimalist Family Hub design, chosen colour scheme and accent, set by an admin for everyone.

The design is a pair of stylesheets under static/looks/minimalist/ (tokens.css for the colours and sizes,
bundle.css for the components). Its four schemes (Dark, Light, Paper, Blueprint) are one ink on one ground;
an admin may pick the accent hue, which Dark and Light take as their accent and Blueprint as its one ink.

The "scene" is the visual at the top of Home (visuals.py): "daily" for a different one each day, or the id of
one an admin has pinned.
"""
import re
from pathlib import Path

from . import db

LOOKS_DIR = Path(__file__).parent / "static" / "looks"

# id, name, Google Fonts address, [(scheme id, name)], set piece, strip separator
DESIGNS = [
    ("minimalist", "Minimalist Family Hub",
     "https://fonts.googleapis.com/css2?family=Archivo:ital,wdth,wght@0,62..125,100..900;1,62..125,100..900&display=swap",
     [("dark", "Dark"), ("light", "Light"), ("paper", "Paper"), ("blueprint", "Blueprint")],
     {"title": "Scope", "state": "Online", "transition": "Screen-change scan"},
     " / "),
]
DAILY = "daily"
VISUAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}")
SWATCH = ["bg", "panel", "ink", "accent", "line2"]
# Accent presets, as the design's picker offers them. None is the design's own accent.
ACCENTS = [("Purple", 295), ("Red", 25), ("Orange", 55), ("Green", 150), ("Teal", 195), ("Blue", 265), ("Pink", 350)]
DEFAULT_HUE = 295
_swatches = {}


def _theme_colours(design):
    """{scheme: {role: colour}} read from the design's tokens.css, for the swatches on the Admin page."""
    if design not in _swatches:
        text = (LOOKS_DIR / design / "tokens.css").read_text(encoding="utf-8")
        found = {}
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
            names = re.findall(r'data-theme="([\w-]+)"', selectors)
            roles = dict(re.findall(r"--([\w-]+):\s*([^;]+);", body))
            for name in names:
                found.setdefault(name, {}).update(roles)
        _swatches[design] = found
    return _swatches[design]


def catalogue():
    """The design and its schemes, as the Admin page lists them."""
    out = []
    for did, name, fonts, schemes, piece, sep in DESIGNS:
        colours = _theme_colours(did)
        first = colours.get(schemes[0][0], {})
        out.append({
            "id": did, "name": name, "fonts": fonts, "piece": piece, "sep": sep,
            "schemes": [{"id": sid, "name": sname,
                         # A scheme that only changes some roles inherits the rest from the first one.
                         "swatch": [colours.get(sid, {}).get(r) or first.get(r, "") for r in SWATCH]}
                        for sid, sname in schemes],
            "accents": [{"name": n, "hue": h} for n, h in ACCENTS], "default_hue": DEFAULT_HUE,
        })
    return out


def _design(did):
    return next((d for d in DESIGNS if d[0] == did), DESIGNS[0])


def _hue(value):
    """A stored accent hue, or None for the design's own accent."""
    try:
        hue = int(value)
    except (TypeError, ValueError):
        return None
    return hue if 0 <= hue <= 359 else None


def current(con):
    """The look everyone sees now. Unknown or missing values (an old design's, say) fall back to the first of each."""
    d = _design(db.kv_get(con, "look:design") or "")
    theme = db.kv_get(con, "look:theme") or ""
    if theme not in [s[0] for s in d[3]]:
        theme = d[3][0][0]
    scene = db.kv_get(con, "look:scene") or ""
    if not VISUAL_ID.fullmatch(scene):
        scene = DAILY
    return {"design": d[0], "theme": theme, "scene": scene, "fonts": d[2], "piece": d[4], "sep": d[5],
            "transition": db.kv_get(con, "look:transition", "on") != "off",
            "accent": _hue(db.kv_get(con, "look:accent"))}


def save(con, data):
    """Change the look for everyone. Returns an error message, or None."""
    d = DESIGNS[0]
    # Check everything before saving anything, so a bad value never leaves half a change.
    if "theme" in data and data["theme"] not in [s[0] for s in d[3]]:
        return "There is no colour scheme called that."
    if "scene" in data and not (isinstance(data["scene"], str) and VISUAL_ID.fullmatch(data["scene"])):
        return "There is no visual called that."
    accent = data.get("accent")
    if accent is not None and (isinstance(accent, bool) or _hue(accent) is None):
        return "The accent is a hue from 0 to 359."
    db.kv_set(con, "look:design", d[0])
    if "theme" in data:
        db.kv_set(con, "look:theme", data["theme"])
    if "scene" in data:
        db.kv_set(con, "look:scene", data["scene"])
    if "transition" in data:
        db.kv_set(con, "look:transition", "on" if data["transition"] else "off")
    if "accent" in data:
        # The design's own purple is stored as no accent, so the stylesheet's default stays in charge.
        db.kv_set(con, "look:accent", "" if accent is None or _hue(accent) == DEFAULT_HUE else str(_hue(accent)))
    return None


def page(html, look):
    """The front page with the current look already in place, so it never flashes the wrong colours."""
    accent = look.get("accent")
    return (html.replace("__DESIGN__", look["design"]).replace("__THEME__", look["theme"])
            .replace("__ACCENT__", "" if accent is None else 'data-accent="custom" style="--accent-h:%d"' % accent)
            .replace("__FONTS__", look["fonts"]))
