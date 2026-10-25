"""The pictures at the top of Home: small animated drawings ("visuals"), a different one each day.

Each visual is one JavaScript file that draws on an 800 x 340 canvas and adds itself to window.VISUALS
(the visuals repository's README describes them). They come from one of two places:

  built in   static/visuals/builtin/   the copies that ship with Family Hub
  GitHub     VISUALS_REPO in .env      a public repository, checked every hour: the newest visuals/,
                                       lib/ and daily.js of its default branch (or VISUALS_BRANCH)

The libraries they use (d3, Astronomy Engine and so on) ship with Family Hub under
static/visuals/node_modules/, at the same paths npm puts them, so a visual runs unchanged on its own test
page and here.

Visuals run in a sandboxed frame (frame.html: sandbox="allow-scripts", without allow-same-origin), so code
fetched from GitHub can draw and fetch public data but never reach Family Hub's pages, cookies or API. The
page sends the frame the colours and the household's home town by postMessage.
"""
import io
import re
import shutil
import time
import zipfile
from pathlib import Path

import requests

from . import db

STATIC = Path(__file__).parent / "static" / "visuals"
BUILTIN = STATIC / "builtin"
LIBRARIES = STATIC / "node_modules"
CHECK_MINUTES = 60
TIMEOUT = 20
AGENT = {"User-Agent": "FamilyHub/1.0 (self-hosted; visuals)", "Accept": "application/vnd.github+json"}
REPO_RE = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")
PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
MAX_ZIP, MAX_FILE, MAX_TOTAL = 60 * 1024 * 1024, 4 * 1024 * 1024, 30 * 1024 * 1024
# What is taken from the repository: everything else in it (its test page, node_modules) is left alone.
TYPES = {".js": "text/javascript", ".json": "application/json"}


class VisualsError(Exception):
    pass


def _folder(hub):
    return Path(hub.config.data_dir) / "visuals"


def version(hub):
    """The visuals in use: "builtin", or the first 12 characters of the GitHub commit they came from."""
    v = db.kv_get(hub.con(), "visuals:version") or ""
    return v if v and (_folder(hub) / v).is_dir() else "builtin"


def source(hub, ver=None):
    """The folder the visuals in use (or of one version) are served from."""
    ver = ver or version(hub)
    return BUILTIN if ver == "builtin" else _folder(hub) / ver


def wanted(rel):
    """Whether a path inside the repository is one Family Hub uses, and is safe to keep."""
    parts = rel.split("/")
    if not all(PART_RE.fullmatch(p) for p in parts) or Path(rel).suffix.lower() not in TYPES:
        return False
    if rel == "daily.js":
        return True
    if parts[0] == "visuals":
        return len(parts) == 2 and rel.endswith(".js")
    return parts[0] == "lib" and 2 <= len(parts) <= 4


def scripts(folder):
    """The scripts a frame loads, in order: lib/ first (alphabetically), then every visual."""
    libs = sorted(p.relative_to(folder).as_posix() for p in (folder / "lib").rglob("*.js")) if (folder / "lib").is_dir() else []
    shown = sorted(p.relative_to(folder).as_posix() for p in (folder / "visuals").glob("*.js")) if (folder / "visuals").is_dir() else []
    return libs, shown


def status(hub):
    con = hub.con()
    config = hub.config
    ver = version(hub)
    return {"repo": config.visuals_repo, "branch": config.visuals_branch, "version": ver,
            "source": "builtin" if ver == "builtin" else "github",
            "checked": db.kv_get(con, "visuals:checked") or "", "error": db.kv_get(con, "visuals:error") or "",
            "count": len(scripts(source(hub))[1])}


def _note(hub, error=""):
    con = hub.con()
    db.kv_set(con, "visuals:checked", db.now())
    db.kv_set(con, "visuals:error", error)


def due(hub):
    checked = db.kv_get(hub.con(), "visuals:checked_at") or "0"
    return time.time() - float(checked) >= CHECK_MINUTES * 60


def sync(hub, force=False):
    """Bring the visuals up to date with VISUALS_REPO. Returns the status. Without a repository, the built-in
    visuals are used. A failed check keeps the visuals already in use."""
    config, con = hub.config, hub.con()
    repo, branch = (config.visuals_repo or "").strip().strip("/"), (config.visuals_branch or "").strip()
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):].removesuffix(".git").strip("/")
    if not repo:
        if db.kv_get(con, "visuals:version"):
            db.kv_set(con, "visuals:version", "")
        _note(hub)
        return status(hub)
    if not force and not due(hub):
        return status(hub)
    db.kv_set(con, "visuals:checked_at", str(time.time()))
    try:
        if not REPO_RE.fullmatch(repo) or (branch and not BRANCH_RE.fullmatch(branch)):
            raise VisualsError("VISUALS_REPO should look like your-name/repository-name.")
        try:
            r = requests.get("https://api.github.com/repos/%s/commits/%s" % (repo, branch or "HEAD"), headers=AGENT, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise VisualsError("Could not reach GitHub: %s" % str(exc)[:120])
        if r.status_code in (404, 422):
            raise VisualsError("GitHub has no public repository %s%s." % (repo, " with a branch " + branch if branch else ""))
        if r.status_code in (403, 429):
            raise VisualsError("GitHub asked for fewer checks for now; trying again later.")
        if r.status_code != 200:
            raise VisualsError("GitHub answered %s." % r.status_code)
        sha = (r.json().get("sha") or "")[:40]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise VisualsError("GitHub's answer had no commit in it.")
        ver = sha[:12]
        if ver != version(hub) or not (_folder(hub) / ver).is_dir():
            _fetch(hub, repo, sha, ver)
        _note(hub)
    except VisualsError as exc:
        _note(hub, str(exc))
    return status(hub)


def _fetch(hub, repo, sha, ver):
    try:
        r = requests.get("https://codeload.github.com/%s/zip/%s" % (repo, sha), headers=AGENT, timeout=60, stream=True)
        if r.status_code != 200:
            raise VisualsError("Could not download the repository (%s)." % r.status_code)
        data = r.raw.read(MAX_ZIP + 1, decode_content=True)
    except requests.RequestException as exc:
        raise VisualsError("Could not download the repository: %s" % str(exc)[:120])
    if len(data) > MAX_ZIP:
        raise VisualsError("The repository is too big to download (the limit is 60 MB). Leave node_modules out of it.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise VisualsError("GitHub's download was not a zip.")
    base = _folder(hub)
    tmp = base / (".new-" + ver)
    shutil.rmtree(tmp, ignore_errors=True)
    total = 0
    for info in z.infolist():
        if info.is_dir() or "/" not in info.filename:
            continue
        rel = info.filename.split("/", 1)[1]  # GitHub puts everything in one "repo-sha/" folder
        if not wanted(rel):
            continue
        if info.file_size > MAX_FILE:
            continue
        total += info.file_size
        if total > MAX_TOTAL:
            shutil.rmtree(tmp, ignore_errors=True)
            raise VisualsError("The visuals and lib folders are too big (the limit is 30 MB).")
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(info))
    if not scripts(tmp)[1]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise VisualsError("The repository has no visuals/ folder with .js files in it.")
    final = base / ver
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    db.kv_set(hub.con(), "visuals:version", ver)
    for old in base.iterdir():
        if old.is_dir() and old.name != ver:
            shutil.rmtree(old, ignore_errors=True)


FRAME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visual</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,100..900&display=swap">
<style>
  html,body{margin:0;background:var(--bg,transparent);overflow:hidden}
  canvas{display:block;width:100%%;height:auto}
</style></head>
<body>
<canvas id="visual" width="800" height="340" role="img" aria-label="Today's visual"></canvas>
%(libraries)s
%(scripts)s
<script src="frame.js"></script>
</body></html>
"""
LIBRARY_ORDER = ["node_modules/astronomy-engine/astronomy.browser.min.js", "node_modules/d3/dist/d3.min.js",
                 "node_modules/topojson-client/dist/topojson-client.min.js", "node_modules/smiles-drawer/dist/smiles-drawer.min.js"]


def frame_html(hub, ver):
    libs, shown = scripts(source(hub, ver))
    tag = lambda src: '<script src="%s"></script>' % src
    has_daily = (source(hub, ver) / "daily.js").is_file()
    return FRAME % {"libraries": "\n".join(tag(s) for s in LIBRARY_ORDER),
                    "scripts": "\n".join([tag(s) for s in libs + shown] + ([tag("daily.js")] if has_daily else []))}


def file_for(hub, ver, rel):
    """The file a frame asks for (one of the libraries, or a visual's own file), or None."""
    if rel.startswith("node_modules/"):
        path = (LIBRARIES / rel[len("node_modules/"):]).resolve()
        return path if LIBRARIES.resolve() in path.parents and path.is_file() else None
    if rel == "frame.js":
        return STATIC / "frame.js"
    if not wanted(rel):
        return None
    folder = source(hub, ver)
    path = (folder / rel).resolve()
    return path if folder.resolve() in path.parents and path.is_file() else None


def check(repo, branch=""):
    """Whether GitHub has that repository (and branch), for the Test button: (ok, words)."""
    repo = (repo or "").strip().strip("/").removeprefix("https://github.com/").removesuffix(".git")
    if not REPO_RE.fullmatch(repo) or (branch and not BRANCH_RE.fullmatch(branch)):
        return False, "Write it as your-name/repository-name."
    try:
        r = requests.get("https://api.github.com/repos/%s/commits/%s" % (repo, branch or "HEAD"), headers=AGENT, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, "Could not reach GitHub: %s" % str(exc)[:120]
    if r.status_code == 200:
        return True, "Found %s on GitHub. Save, then press Check for new visuals under Appearance." % repo
    if r.status_code in (404, 422):
        return False, "GitHub has no public repository %s%s." % (repo, " with a branch " + branch if branch else "")
    return False, "GitHub answered %s." % r.status_code


def known_version(hub, ver):
    return ver == "builtin" or (re.fullmatch(r"[0-9a-f]{12}", ver or "") and (_folder(hub) / ver).is_dir())
