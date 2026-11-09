"""The look of the whole hub: the Minimalist Family Hub design, chosen colour scheme and accent, set by an admin for everyone.

The design is a pair of stylesheets under static/looks/minimalist/ (tokens.css for the colours and sizes,
bundle.css for the components). Its four schemes (Dark, Light, Paper, Blueprint) are one ink on one ground;
an admin may pick the accent hue, which Dark and Light take as their accent and Blueprint as its one ink.

The "scene" is the visual at the top of Home (visuals.py): "daily" for a different one each day, or the id of
one an admin has pinned.
"""
import re
from pathlib import Path
from typing import NamedTuple

from . import db

LOOKS_DIR = Path(__file__).parent / "static" / "looks"


class Design(NamedTuple):
    id: str
    name: str
    fonts: str     # the font stylesheet's address
    schemes: list  # [(scheme id, name)], the first is the fallback
    piece: dict    # the set piece's words
    sep: str       # what goes between bits of the strip


DESIGNS = [
    Design("minimalist", "Minimalist Family Hub",
     "/static/fonts/archivo.css",  # served by Family Hub, so the look holds with no internet
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
    listed = []
    for design in DESIGNS:
        colours = _theme_colours(design.id)
        first = colours.get(design.schemes[0][0], {})
        listed.append({
            "id": design.id, "name": design.name, "fonts": design.fonts, "piece": design.piece, "sep": design.sep,
            "schemes": [{"id": scheme_id, "name": scheme_name,
                         # lazy schemes only override a few colours and borrow the rest from the first one
                         "swatch": [colours.get(scheme_id, {}).get(role) or first.get(role, "") for role in SWATCH]}
                        for scheme_id, scheme_name in design.schemes],
            "accents": [{"name": name, "hue": hue} for name, hue in ACCENTS], "default_hue": DEFAULT_HUE,
        })
    return listed


def _scheme_ids(design):
    return [scheme_id for scheme_id, _ in design.schemes]


def _design(design_id):
    return next((design for design in DESIGNS if design.id == design_id), DESIGNS[0])


def _hue(value):
    """A stored accent hue, or None for the design's own accent."""
    try:
        hue = int(value)
    except (TypeError, ValueError):
        return None
    return hue if 0 <= hue <= 359 else None


def current(con):
    """The look everyone sees now. Unknown or missing values (an old design's, say) fall back to the first of each."""
    design = _design(db.kv_get(con, "look:design") or "")
    theme = db.kv_get(con, "look:theme") or ""
    if theme not in _scheme_ids(design):
        theme = design.schemes[0][0]
    scene = db.kv_get(con, "look:scene") or ""
    if not VISUAL_ID.fullmatch(scene):
        scene = DAILY
    return {"design": design.id, "theme": theme, "scene": scene, "fonts": design.fonts, "piece": design.piece, "sep": design.sep,
            "transition": db.kv_get(con, "look:transition", "on") != "off",
            "accent": _hue(db.kv_get(con, "look:accent")),
            # an admin's switch: when on, each profile that picked a colour sees the hub in it instead of the household accent
            "personal": db.kv_get(con, "look:personal", "off") == "on",
            "accents": [{"name": name, "hue": hue} for name, hue in ACCENTS]}


def profile_hues(con):
    """Each profile's own accent hue, for those that picked one: {profile id: hue}."""
    rows = con.execute("SELECT key, value FROM kv WHERE key LIKE 'look:hue:%'").fetchall()
    return {int(row[0].rsplit(":", 1)[1]): hue for row in rows if (hue := _hue(row[1])) is not None}


def set_profile_hue(con, profile_id, value):
    """A profile's own colour, a hue from 0 to 359, or None to follow the household. Returns an error message or None."""
    if value is not None and (isinstance(value, bool) or _hue(value) is None):
        return "The colour is a hue from 0 to 359."
    db.kv_set(con, "look:hue:%d" % profile_id, "" if value is None else str(_hue(value)))
    return None


def save(con, data):
    """Change the look for everyone. Returns an error message, or None."""
    design = DESIGNS[0]
    # check it ALL first, then save. otherwise one bad value leaves you with half a theme
    if "theme" in data and data["theme"] not in _scheme_ids(design):
        return "There is no colour scheme called that."
    if "scene" in data and not (isinstance(data["scene"], str) and VISUAL_ID.fullmatch(data["scene"])):
        return "There is no visual called that."
    accent = data.get("accent")
    if accent is not None and (isinstance(accent, bool) or _hue(accent) is None):
        return "The accent is a hue from 0 to 359."
    db.kv_set(con, "look:design", design.id)
    if "theme" in data:
        db.kv_set(con, "look:theme", data["theme"])
    if "scene" in data:
        db.kv_set(con, "look:scene", data["scene"])
    if "transition" in data:
        db.kv_set(con, "look:transition", "on" if data["transition"] else "off")
    if "personal" in data:
        db.kv_set(con, "look:personal", "on" if data["personal"] else "off")
    if "accent" in data:
        # picking the default purple saves "nothing", so the stylesheet stays the boss of its own colour
        db.kv_set(con, "look:accent", "" if accent is None or _hue(accent) == DEFAULT_HUE else str(_hue(accent)))
    return None


def page(html, look):
    """The front page with the current look already in place, so it never flashes the wrong colours."""
    accent = look.get("accent")
    return (html.replace("__DESIGN__", look["design"]).replace("__THEME__", look["theme"])
            .replace("__ACCENT__", "" if accent is None else 'data-accent="custom" style="--accent-h:%d"' % accent)
            .replace("__FONTS__", look["fonts"]))
