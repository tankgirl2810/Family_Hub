"""Settings, read from environment variables (see .env.example)."""
import os
from pathlib import Path


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _path(name, default=None):
    value = os.environ.get(name) or default
    return Path(value).expanduser() if value else None


class Config:
    def __init__(self):
        self.data_dir = _path("BOOKHUB_DATA_DIR", "./data")
        self.library_dir = _path("CALIBRE_LIBRARY", "./library")
        self.ingest_dir = _path("INGEST_DIR")
        self.downloads_dir = _path("DOWNLOADS_DIR")
        self.inbox_dir = _path("INBOX_DIR")
        self.attention_dir = _path("ATTENTION_DIR")
        self.tagpacks_dir = _path("TAGPACKS_DIR", str(Path(__file__).resolve().parents[2] / "tagpacks"))
        self.port = int(os.environ.get("BOOKHUB_PORT", "8090"))

        self.guest_enabled = _bool("GUEST_ENABLED", True)
        self.guest_lan_only = _bool("GUEST_LAN_ONLY", True)
        self.trust_proxy = _bool("TRUST_PROXY", False)

        self.cwa_url = (os.environ.get("CWA_URL") or "").rstrip("/")
        # For the one job that changes the library: removing spare copies (docs/08). Family Hub signs in
        # to the library manager and asks it to do the work.
        self.cwa_api_url = (os.environ.get("CWA_INTERNAL_URL") or "").rstrip("/")
        self.cwa_user = os.environ.get("CWA_USER") or ""
        self.cwa_pass = os.environ.get("CWA_PASS") or ""
        self.prowlarr_url = (os.environ.get("PROWLARR_URL") or "").rstrip("/")
        self.prowlarr_api_key = os.environ.get("PROWLARR_API_KEY") or ""
        self.qbit_url = (os.environ.get("QBITTORRENT_URL") or "").rstrip("/")
        self.qbit_user = os.environ.get("QBITTORRENT_USER") or ""
        self.qbit_pass = os.environ.get("QBITTORRENT_PASS") or ""
        self.qbit_category = os.environ.get("QBITTORRENT_CATEGORY") or ""

        # Sending books to a Kindle by email. Empty values fall back to settings.DEFAULTS.
        self.smtp_host = os.environ.get("SMTP_HOST") or ""
        self.smtp_port = os.environ.get("SMTP_PORT") or ""
        self.smtp_user = os.environ.get("SMTP_USER") or ""
        self.smtp_pass = os.environ.get("SMTP_PASS") or ""
        self.smtp_from = os.environ.get("SMTP_FROM") or ""
        self.smtp_max_mb = os.environ.get("SMTP_MAX_MB") or ""

        # Community tags from public catalogues. "on" unless switched off; see settings.DEFAULTS.
        self.tag_lookups = (os.environ.get("TAG_LOOKUPS") or "").strip().lower()
        self.google_books_key = os.environ.get("GOOGLE_BOOKS_KEY") or ""
        self.comic_vine_key = os.environ.get("COMIC_VINE_KEY") or ""
        self.hardcover_key = os.environ.get("HARDCOVER_KEY") or ""

        # Series read chapter by chapter (manga, manhwa), kept by a Suwayomi server.
        self.series_url = (os.environ.get("SUWAYOMI_URL") or "").rstrip("/")
        self.series_user = os.environ.get("SUWAYOMI_USER") or ""
        self.series_pass = os.environ.get("SUWAYOMI_PASS") or ""
        self.series_langs = os.environ.get("SERIES_LANGUAGES") or ""

        # The nightly copy to a second disk (docs/06). Off until SECOND_DISK_DIR names a folder that exists.
        self.second_disk_dir = _path("SECOND_DISK_DIR")
        self.stack_dir = _path("STACK_FOLDER")
        self.series_dir = _path("SERIES_DIR")
        self.second_copy = (os.environ.get("SECOND_COPY") or "").strip().lower()

        # Films and TV (docs/14). An empty API key is read from the service's own settings file.
        self.radarr_url = (os.environ.get("RADARR_URL") or "").rstrip("/")
        self.radarr_api_key = os.environ.get("RADARR_API_KEY") or ""
        self.sonarr_url = (os.environ.get("SONARR_URL") or "").rstrip("/")
        self.sonarr_api_key = os.environ.get("SONARR_API_KEY") or ""
        self.bazarr_url = (os.environ.get("BAZARR_URL") or "").rstrip("/")
        self.bazarr_api_key = os.environ.get("BAZARR_API_KEY") or ""
        self.subtitle_langs = os.environ.get("SUBTITLE_LANGUAGES") or ""
        self.opensubtitles_user = os.environ.get("OPENSUBTITLES_USER") or ""
        self.opensubtitles_pass = os.environ.get("OPENSUBTITLES_PASS") or ""
        self.jellyfin_url = (os.environ.get("JELLYFIN_URL") or "").rstrip("/")
        self.jellyfin_api_key = os.environ.get("JELLYFIN_API_KEY") or ""
        self.jellyfin_link = (os.environ.get("JELLYFIN_LINK") or "").rstrip("/")
        self.gluetun_url = (os.environ.get("GLUETUN_URL") or "").rstrip("/")
        self.gluetun_api_key = os.environ.get("GLUETUN_API_KEY") or ""
        self.vpn_provider = os.environ.get("VPN_PROVIDER") or ""
        # One sign-in for the programs' own pages (Radarr, Sonarr, Prowlarr, Bazarr, qBittorrent), set on each
        # by Connect everything.
        self.services_user = os.environ.get("SERVICES_USER") or ""
        # The visuals at the top of Home (visuals.py): where new ones come from, and the home town they use.
        self.visuals_repo = os.environ.get("VISUALS_REPO") or ""
        self.visuals_branch = os.environ.get("VISUALS_BRANCH") or ""
        self.home_place = os.environ.get("HOME_PLACE") or ""
        self.home_lat = os.environ.get("HOME_LAT") or ""
        self.home_lon = os.environ.get("HOME_LON") or ""
        self.services_pass = os.environ.get("SERVICES_PASSWORD") or ""
        # Set by docker-compose.yml when the VPN's key or user name is filled in. The VPN itself is set in .env.
        self.vpn_configured = bool(os.environ.get("VPN_CONFIGURED"))
        self.media_root = _path("MEDIA_ROOT_DIR", "/media-root")
        # The ports docker-compose.yml publishes, for the admin's links to each program's own page.
        self.service_ports = {name: _int(var, default) for name, var, default in (
            ("cwa", "CWA_PORT", 8083), ("prowlarr", "PROWLARR_PORT", 9696), ("qbittorrent", "QBITTORRENT_PORT", 8080),
            ("radarr", "RADARR_PORT", 7878), ("sonarr", "SONARR_PORT", 8989), ("bazarr", "BAZARR_PORT", 6767),
            ("jellyfin", "JELLYFIN_PORT", 8096), ("suwayomi", "SUWAYOMI_PORT", 4567), ("komga", "KOMGA_PORT", 25600))}

        self.background_jobs =_bool("BACKGROUND_JOBS", True)
        self.backup_keep = int(os.environ.get("BACKUP_KEEP", "30"))

    @property
    def db_path(self):
        return self.data_dir / "bookhub.db"

    @property
    def backups_dir(self):
        return self.data_dir / "backups"

    @property
    def converted_dir(self):
        return self.data_dir / "converted"

    @property
    def metadata_db(self):
        return self.library_dir / "metadata.db"

    @property
    def mail_configured(self):
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)

    @property
    def mail_sender(self):
        return self.smtp_from or self.smtp_user

    @property
    def discover_configured(self):
        return bool(self.prowlarr_url and self.prowlarr_api_key)
