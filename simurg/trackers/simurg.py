"""Simurg tracker."""

from simurg.trackers.base import BaseGazelleApi


class SimurgApi(BaseGazelleApi):
    site_code = "SIM"
    base_url = "https://simurg.world"
    tracker_url = "https://tracker.simurg.world"
    site_string = "SIM"

    def __init__(self):
        # Load config values before calling super().__init__ which will authenticate
        try:
            from simurg.config import get_config

            cfg = get_config()
            tcfg = cfg.get_tracker_cfg("simurg")
            session = tcfg.get("session", "")
            api_key = tcfg.get("api_key", "")
            dott = tcfg.get("dottorrents_dir", None)
            if not dott:
                dott = cfg.directory.get("dottorrents_dir", ".torrents")
            self.cookie = str(session) if session else ""
            if api_key:
                self.api_key = str(api_key)
            self.dot_torrents_dir = str(dott)
        except Exception:
            # allow dry-run without config
            self.cookie = ""
            self.dot_torrents_dir = ".torrents"
        super().__init__()
