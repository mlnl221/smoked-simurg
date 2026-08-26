"""Minimal repo-local config loader: ./config.toml via tomllib/tomli."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from simurg.errors import ConfigError


def _find_config() -> Path:
    # Prefer ./config.toml in repo root (cwd or package parent)
    candidates = [
        Path.cwd() / "config.toml",
        Path(__file__).resolve().parents[1] / "config.toml",
        Path(__file__).resolve().parents[2] / "config.toml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "config.toml not found. Copy config.example.toml -> config.toml and fill in values.\n"
        f"Tried: {', '.join(str(c) for c in candidates)}"
    )


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


class _CfgSection:
    def __init__(self, data: dict):
        self._data = data or {}

    def __getattr__(self, name):
        if name in self._data:
            val = self._data[name]
            if isinstance(val, dict):
                return _CfgSection(val)
            return val
        raise AttributeError(name)

    def get(self, key, default=None):
        val = self._data.get(key, default)
        if isinstance(val, dict):
            return _CfgSection(val)
        return val

    def __bool__(self):
        return bool(self._data)

    def __repr__(self):
        return repr(self._data)


class Config:
    def __init__(self, data: dict):
        self._data = data
        self.directory = _CfgSection(data.get("directory", {}))
        self.image = _CfgSection(data.get("image", {}))
        self.metadata = _CfgSection(data.get("metadata", {}))
        self.tracker = _CfgSection(data.get("tracker", {}))
        self.upload = _CfgSection(data.get("upload", {}))

    def get_tracker_cfg(self, code: str = "simurg") -> _CfgSection:
        # tracker.simurg or tracker[simurg]
        t = self._data.get("tracker", {})
        if code in t:
            return _CfgSection(t[code])
        # also support [tracker.simurg] nesting already handled
        return _CfgSection({})


# Simple global - lazily loaded, can be reloaded
_cfg: Config | None = None
_cfg_path: Path | None = None


def load_config(path: Path | None = None) -> Config:
    global _cfg, _cfg_path
    if path is None:
        try:
            path = _find_config()
        except FileNotFoundError as e:
            raise ConfigError(str(e)) from e
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = _load_toml(path)
    except Exception as e:
        raise ConfigError(f"Failed to parse {path}: {e}") from e
    _cfg = Config(data)
    _cfg_path = path
    return _cfg


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        return load_config()
    return _cfg


def get_config_path() -> Path:
    if _cfg_path:
        return _cfg_path
    try:
        return _find_config()
    except FileNotFoundError:
        return Path.cwd() / "config.toml"
