"""LyriCool — configuration + token storage."""

import json
import os
import sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".lyricool-config.json"

# Legacy path from when the project was named `apple-lyrics`. We migrate the
# file silently on first load so existing users don't lose their tokens.
_LEGACY_CONFIG_PATH = Path.home() / ".apple-lyrics-config.json"


DEFAULT_CONFIG = {
    "authorization": "",
    "media_user_token": "",
    "storefront": "us",
    "language": "en-US",
}


def _migrate_legacy_config():
    """If the new config doesn't exist but the old one does, move it in place."""
    if not CONFIG_PATH.exists() and _LEGACY_CONFIG_PATH.exists():
        try:
            _LEGACY_CONFIG_PATH.rename(CONFIG_PATH)
            print(
                f"Migrated {_LEGACY_CONFIG_PATH} → {CONFIG_PATH}",
                file=sys.stderr,
            )
        except OSError as e:
            print(
                f"  (warning: couldn't migrate legacy config: {e})",
                file=sys.stderr,
            )


def load_config():
    """Load config from file, env vars, or return defaults."""
    _migrate_legacy_config()
    config = DEFAULT_CONFIG.copy()

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            config.update(json.load(f))

    # Env vars override file config. Names kept as-is for backwards compat —
    # the Apple-Music tokens themselves are unchanged, only the project
    # around them was renamed.
    if os.environ.get("APPLE_MUSIC_AUTH"):
        config["authorization"] = os.environ["APPLE_MUSIC_AUTH"]
    if os.environ.get("APPLE_MUSIC_MEDIA_TOKEN"):
        config["media_user_token"] = os.environ["APPLE_MUSIC_MEDIA_TOKEN"]
    if os.environ.get("APPLE_MUSIC_STOREFRONT"):
        config["storefront"] = os.environ["APPLE_MUSIC_STOREFRONT"]

    return config


def save_config(config):
    """Save config to file."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to {CONFIG_PATH}")


def setup_tokens(authorization, media_user_token, storefront=None):
    """Save tokens to config file."""
    config = load_config()
    config["authorization"] = authorization
    config["media_user_token"] = media_user_token
    if storefront:
        config["storefront"] = storefront
    save_config(config)


def validate_config(config):
    """Check that required tokens are present."""
    if not config.get("authorization"):
        raise ValueError(
            "Missing Authorization token. Run: lyricool setup\n"
            "Or set APPLE_MUSIC_AUTH env var."
        )
    if not config.get("media_user_token"):
        raise ValueError(
            "Missing Media-User-Token. Run: lyricool setup\n"
            "Or set APPLE_MUSIC_MEDIA_TOKEN env var."
        )
