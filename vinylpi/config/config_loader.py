import json
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)
from pathlib import Path
from vinylpi.paths import BASE_DIR, CONFIG_PATH, get_active_config_path
from vinylpi.core.display_layout import normalize_image_config

_LEGACY_CONFIG_PATH = CONFIG_PATH

CONFIG_DEFAULTS = {
    "audio": {
        "device_name_contains": "",
        "sample_seconds": 4,
        "sample_rate": 44100,
        "channels": 1,
        "adaptive_sample": {
            "enabled": False,
            "failure_durations_seconds": [6, 8]
        },
        "pickup_usage": {
            "enabled": True,
            "start_threshold_dbfs": -48.0,
            "stop_threshold_dbfs": -61.0,
            "start_confirm_seconds": 3.0,
            "stop_confirm_seconds": 30.0
        }
    },
    "image": {
        "canvas_size": 64,
        "show_cover": True,
        "show_artist": True,
        "show_title": True,
        "show_album": False,
        "top_margin": 1,
        "cover_size": 46,
        "margin_image_text": 3,
        "line_spacing_margin": 3,
        "font_path": "assets/fonts/vinylpixel.ttf",
        "font_size": 5,
        "use_dynamic_bg": True,
        "manual_bg_color": [0, 0, 0],
        "use_dynamic_text_color": True,
        "invert_dynamic_colors": False,
        "text_color": [255, 255, 255],
        "uppercase": True,
        "preview_scale": 8,
        "marquee_speed": 20,
        "sleep_seconds": 0.01
    },
    "divoom": {
        "ip": "",
        "device_name": "",
        "device_id": 0,
        "device_mac": "",
        "timeout": 2.0,
        "auto_reset_gif_id": False,
        "animation_mode": "stream",  # stream | gif_url
        "discovery": {
            "enabled": True,
            "subnet_prefix": "192.168.2.",
            "ip_range_start": 100,
            "ip_range_end": 199
        }
    },
    "shazam": {
        "timeout_seconds": 15
    },
    "spotify": {
        "poll_seconds": 2.0,
        "auto_sleep_enabled": True,
        "auto_sleep_iterations": 30
    },
    "discogs": {
        "enabled": False,
        "username": "",
        "prefer_collection": True,
        "cover_source": "shazam",  # Preferred artwork for recognized vinyl tracks.
        "sequence_matching": True,
        "infer_unrecognized_next": True,
        "vinyl_only": True,
        "min_match_confidence": 0.72
    },
    "debug": {
        "logs": True,
        "pixoo_frame_path": "",
        "preview_path": "",
        "wav_path": ""
    },
    "fallback": {
        "enabled": True,
        "image_path": "assets/fallback/normal/Fallback.png",
        "side_flip_enabled": True,
        "side_flip_image_path": "assets/fallback/turn/turn_record.png",
        "allowed_failures": 3
    },
    "behavior": {
        "loop_delay_seconds": 1,
        "vinyl_auto_wake_enabled": True,
        "vinyl_auto_wake_threshold_dbfs": -48.0,
        "vinyl_auto_wake_confirm_seconds": 2.0,
        "vinyl_auto_sleep_enabled": True,
        "vinyl_manual_shutdown_only": True,
        "vinyl_auto_sleep_iterations": 30,
        "vinyl_audio_sleep_enabled": True,
        "vinyl_audio_sleep_threshold_dbfs": -61.0,
        "vinyl_audio_sleep_confirm_seconds": 30.0,
        "stats_min_consecutive": 3
    },
    "pixoo_automations": {
        "turn_on_on_start": False,
        "off_action": "none",  # none | community_gif | brightness_off
        "stop_gif_file_id": ""
    },
    "homeassistant": {
        "use_ha": True,
        "base_url": "",
        "webhook_id": "vinylpi_cover_color",
        # Pixoo actions are handled locally; HA is used only for color sync.
    }
}

def deep_update(base: dict, updates: dict) -> dict:
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base

def load_config(path: Path | str | None = None) -> dict:
    if path is not None:
        path = Path(path)
    elif Path(CONFIG_PATH) != Path(_LEGACY_CONFIG_PATH):
        path = Path(CONFIG_PATH)
    else:
        path = get_active_config_path()
    cfg = deepcopy(CONFIG_DEFAULTS)
    user_cfg = {}

    try:
        user_cfg = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(user_cfg, dict):
            deep_update(cfg, user_cfg)
            # Migrate the former Vinyl-only ``behavior.auto_sleep`` threshold to
            # the explicit per-source power-saving settings. A value <= 0 used
            # to disable auto sleep, so preserve that behavior as the new toggle.
            user_behavior = user_cfg.get("behavior") or {}
            if isinstance(user_behavior, dict) and "auto_sleep" in user_behavior:
                legacy_value = user_behavior.get("auto_sleep")
                try:
                    legacy_iterations = int(legacy_value)
                except (TypeError, ValueError):
                    legacy_iterations = 30

                behavior_cfg = cfg.setdefault("behavior", {})
                if "vinyl_auto_sleep_enabled" not in user_behavior:
                    behavior_cfg["vinyl_auto_sleep_enabled"] = legacy_iterations > 0
                if "vinyl_auto_sleep_iterations" not in user_behavior:
                    behavior_cfg["vinyl_auto_sleep_iterations"] = max(1, legacy_iterations or 30)
        else:
            logger.error("Configuration is not a JSON object (%s). Using defaults.", path.name)
    except FileNotFoundError:
        # Expected on a newly created profile: defaults are the initial config.
        pass
    except (json.JSONDecodeError, UnicodeError):
        logger.error("Invalid configuration file (%s). Using defaults; check JSON syntax.", path.name)
    except PermissionError:
        logger.error("Configuration file cannot be read (%s). Check file permissions.", path.name)
    except OSError as exc:
        logger.error("Configuration file unavailable (%s). Using defaults: %s", path.name, exc.strerror or type(exc).__name__)

    # Existing profile settings may still reference fallback files from before
    # normal/ and turn/ galleries were split. Redirect only when an identically
    # named file exists in the corresponding new directory. Never overwrite a
    # custom selection or change the file on disk.
    fallback = cfg.get("fallback")
    if isinstance(fallback, dict):
        for field, folder in (("image_path", "normal"), ("side_flip_image_path", "turn")):
            selected = fallback.get(field)
            if not isinstance(selected, str):
                continue
            legacy_path = Path(selected)
            if legacy_path.parent != Path("assets/fallback"):
                continue
            new_path = Path("assets/fallback") / folder / legacy_path.name
            if (BASE_DIR / new_path).is_file():
                fallback[field] = new_path.as_posix()

    # Upgrade settings saved by earlier releases without changing their behavior.
    automation_cfg = cfg.get("pixoo_automations")
    if isinstance(automation_cfg, dict):
        stored = user_cfg.get("pixoo_automations", {}) if isinstance(user_cfg, dict) else {}
        if not isinstance(stored, dict):
            stored = {}
        if "turn_on_on_start" not in stored and "turn_on_on_vinyl_start" in stored:
            automation_cfg["turn_on_on_start"] = bool(stored["turn_on_on_vinyl_start"])
        automation_cfg.pop("turn_on_on_vinyl_start", None)
        if "off_action" not in stored and stored.get("play_gif_on_stop") is True:
            automation_cfg["off_action"] = "community_gif"
        if automation_cfg.get("off_action") not in ("none", "community_gif", "brightness_off"):
            automation_cfg["off_action"] = "none"
        automation_cfg.pop("play_gif_on_stop", None)

    divoom = cfg.get("divoom")
    if isinstance(divoom, dict):
        if divoom.get("animation_mode") not in ("stream", "gif_url"):
            divoom["animation_mode"] = "stream"
        divoom.pop("native_text_font", None)
        divoom.pop("native_text_height", None)

    cfg["image"] = normalize_image_config(cfg.get("image"))

    # Discogs credentials are environment-only. This also removes a legacy
    # token from the in-memory config so the next config write cleans it up.
    discogs = cfg.get("discogs")
    if isinstance(discogs, dict):
        discogs.pop("token", None)

    # Remove superseded Pixoo-via-HA settings from older profile configs.
    homeassistant = cfg.get("homeassistant")
    if isinstance(homeassistant, dict):
        for old_key in ("replay_on_stop_enabled", "stop_webhook_id", "wake_webhook_id", "wake_webhook_enabled"):
            homeassistant.pop(old_key, None)

    # Older builds used a time-based repeat guard. Statistics are now strictly
    # transition-based, so keeping this legacy value would be misleading.
    behavior = cfg.get("behavior")
    if isinstance(behavior, dict):
        behavior.pop("stats_repeat_guard_seconds", None)
        behavior.pop("auto_sleep", None)

    return cfg
