"""Single configuration home for all Stove Pilot flavors."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOGGER = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Environment-driven settings (prefix ``STOVE_PILOT_``)."""

    model_config = SettingsConfigDict(env_prefix="STOVE_PILOT_", env_file=".env", extra="ignore")

    gateway: Literal["navel", "simulator"] = "simulator"
    dev: bool = Field(
        default=False,
        description="Restart this process when a watched source file changes. Development only.",
    )
    simulator_seed: Path | None = None
    navel_base_url: str = "http://192.168.68.54"
    navel_device_id: str = ""
    navel_security: SecretStr = SecretStr("")
    navel_timeout_s: float = 10.0
    navel_min_interval_s: float = 0.2
    navel_chunk_size: int = Field(
        default=8,
        ge=1,
        le=16,
        description="Maximum items per module request; lowered at runtime on blank replies.",
    )

    profile: str = "superior_maira"
    features: set[Literal["chrono"]] = {"chrono"}
    db_path: Path = Path("./data/stove-pilot.sqlite")
    timezone: str = "Europe/Paris"
    poll_interval_s: int = 30
    slow_read_interval_s: int = 300
    sample_interval_s: int = 60
    history_retention_days: int = Field(default=0, ge=0)
    # Reason: exactly this many minutes between starts is compliant (strictly less raises; at most 1440).
    short_cycle_min_interval_min: int = Field(default=90, ge=1, le=1440)
    min_restart_interval_min: int = 15
    safety_enabled_default: bool = False
    safety_threshold_default_c: int = Field(default=10, ge=5, le=20)
    safety_max_attempts_default: int = Field(default=3, ge=1, le=5)
    # Reason: the lower bound is min_restart_interval_min; a lower env value is raised.
    safety_retry_interval_default_min: int = Field(default=15, le=240)
    safety_confirm_polls: int = Field(default=2, ge=1, le=10)
    safety_rearm_hysteresis_c: float = Field(default=1.0, ge=0.5, le=5)
    safety_chrono_stop_window_min: int = Field(default=5, ge=1, le=15)
    deferred_start_expiry_min: int = 45
    deferred_stop_expiry_min: int = 30
    clock_drift_tolerance_s: int = Field(default=300, ge=30, le=3600)
    clock_auto_sync: bool = True
    clock_sync_min_interval_h: int = 6
    live_read_min_interval_s: int = 5
    vacation_setpoint_c: float = Field(default=7.0, ge=7, le=12)

    api_host: str = "127.0.0.1"
    api_port: int = 8710
    api_key: SecretStr = SecretStr("")

    ui_host: str = "0.0.0.0"
    ui_port: int = 8711
    ui_api_base_url: str = "http://127.0.0.1:8710"
    ui_secret_key: SecretStr = SecretStr("")
    ui_frame_ancestors: str = "'self'"
    ui_refresh_default_s: Literal[0, 10, 30, 60, 300] = 30
    ui_history_events_page_size: int = Field(default=25, ge=5, le=100)

    @field_validator("navel_device_id")
    @classmethod
    def _normalize_mac(cls, value: str) -> str:
        return value.replace(":", "").replace("-", "").upper()

    @model_validator(mode="after")
    def _clamp_safety_retry_default(self) -> Self:
        """Raise a retry default that sits below the stove restart guard.

        :return: This settings instance.
        """
        minimum = self.min_restart_interval_min
        if self.safety_retry_interval_default_min < minimum:
            _LOGGER.warning(
                "STOVE_PILOT_SAFETY_RETRY_INTERVAL_DEFAULT_MIN "
                "raised to the minimum restart interval (%s)",
                minimum,
            )
            self.safety_retry_interval_default_min = minimum
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    :return: Cached settings.
    """
    return Settings()
