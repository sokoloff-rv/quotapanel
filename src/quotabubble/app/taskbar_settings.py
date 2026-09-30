from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir
from pydantic import BaseModel, Field

from quotabubble.utils import write_text_atomic

CONFIG_DIR = Path(user_config_dir("QuotaPanel", appauthor=False))
SETTINGS_PATH = CONFIG_DIR / "settings.json"
CACHE_PATH = CONFIG_DIR / "last_good.json"


class PanelSettings(BaseModel):
    width: int = Field(default=280, ge=220, le=420)
    offset: int = Field(default=12, ge=0, le=4000)
    refresh_interval_minutes: int = Field(default=5, ge=1, le=60)
    theme: Literal["system", "dark", "light"] = "system"
    transparent_background: bool = False
    remaining: bool = True
    autostart: bool = False
    hidden: bool = False

    @property
    def refresh_interval_ms(self) -> int:
        return self.refresh_interval_minutes * 60_000

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> PanelSettings:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                # The panel now always shows remaining quota, including when
                # upgrading settings written by the original prototype.
                raw["remaining"] = True
            return cls.model_validate(raw)
        except (OSError, ValueError):
            return cls()

    def save(self, path: Path = SETTINGS_PATH) -> None:
        write_text_atomic(path, self.model_dump_json(indent=2))
