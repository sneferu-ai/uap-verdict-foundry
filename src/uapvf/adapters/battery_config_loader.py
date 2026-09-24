"""Battery configuration loader (spec §5, FR-006).

Loads var/battery_config.yaml, performs ${ENV_VAR} substitution, imports
each adapter class, and constructs it with its params. Any failure — missing
file, empty/zero categories, malformed YAML, unimportable module, missing
class, constructor raise — surfaces as BatteryConfigError, which the
pipeline converts into an unrecoverable case failure (FR-006).
"""
from __future__ import annotations

import importlib
import os
import re
from typing import List, Tuple

import yaml

from uapvf.adapters import BatteryCategoryAdapter, BatteryConfigError
from uapvf.config import get_settings

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _substitute_env(value):
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def load_battery_config(path=None) -> dict:
    settings = get_settings()
    path = path or (settings.var_dir / "battery_config.yaml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError:
        raise BatteryConfigError("battery_config.yaml empty or missing")
    except Exception as exc:
        raise BatteryConfigError(
            f"battery adapter configuration error: malformed YAML ({exc})"
        )
    if not isinstance(raw, dict) or not raw.get("categories"):
        raise BatteryConfigError("battery_config.yaml empty or missing")
    raw = _substitute_env(raw)
    categories = raw.get("categories")
    if not isinstance(categories, list) or len(categories) == 0:
        raise BatteryConfigError("battery_config.yaml empty or missing")
    return raw


def build_adapters(config: dict) -> List[Tuple[str, BatteryCategoryAdapter, float]]:
    """Return [(category_name, adapter_instance, timeout_s)]."""
    adapters = []
    for entry in config.get("categories", []):
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("adapter"):
            raise BatteryConfigError(
                "battery adapter configuration error: category entry missing "
                "name or adapter"
            )
        name = entry["name"]
        dotted = entry["adapter"]
        params = entry.get("params") or {}
        timeout_s = float(entry.get("timeout_s", 10))
        module_path, _, class_name = dotted.rpartition(".")
        if not module_path:
            raise BatteryConfigError(
                f"battery adapter configuration error: bad adapter path {dotted!r}"
            )
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            raise BatteryConfigError(
                f"battery adapter configuration error: cannot import "
                f"{module_path} ({exc.__class__.__name__}: {exc})"
            )
        cls = getattr(module, class_name, None)
        if cls is None:
            raise BatteryConfigError(
                f"battery adapter configuration error: class {class_name} not "
                f"found in {module_path}"
            )
        try:
            instance = cls(params)
        except Exception as exc:
            raise BatteryConfigError(
                f"battery adapter configuration error: constructor for {name} "
                f"raised ({exc})"
            )
        if not isinstance(instance, BatteryCategoryAdapter):
            raise BatteryConfigError(
                f"battery adapter configuration error: {dotted} does not "
                "implement BatteryCategoryAdapter"
            )
        adapters.append((name, instance, timeout_s))
    return adapters
