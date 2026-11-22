"""Constants for the Legrand RFLC integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.config_entries import ConfigEntry

from .hub import Hub

DOMAIN: Final = "legrand_rflc"

# Seconds to wait for the hub to reply to a command.
COMMAND_TIMEOUT: Final = 5.0


@dataclass
class LegrandRFLCData:
    """Runtime data for the Legrand RFLC integration."""

    hub: Hub


type LegrandRFLCConfigEntry = ConfigEntry[LegrandRFLCData]
