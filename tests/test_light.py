"""Tests for the Legrand RFLC light platform."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_ICON,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.legrand_rflc import light
from custom_components.legrand_rflc.const import DOMAIN, LegrandRFLCData
from custom_components.legrand_rflc.hub import Composer

LIGHT_PATCH = "custom_components.legrand_rflc.light"

from .conftest import MOCK_HOST, NO_REPLY, MockHub


@pytest.fixture
async def setup_lights(setup_integration: MockHub) -> MockHub:
    """Set up the integration and return the mock hub."""
    return setup_integration


# ---- Entity creation ----


async def test_dimmer_entity_created(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test a dimmer zone creates a light entity with brightness."""
    state = hass.states.get("light.living_room")
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes.get(ATTR_BRIGHTNESS) is not None


async def test_switch_entity_created(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test a switch zone creates a light entity without brightness."""
    state = hass.states.get("light.kitchen")
    assert state is not None
    assert state.state == STATE_OFF


# ---- Icons ----


async def test_lights_use_domain_default_icons(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Lights set no icon, so the light domain's on/off icons apply."""
    icons = json.loads(
        (Path(light.__file__).parent / "icons.json").read_text(encoding="utf-8")
    )
    assert "entity" not in icons
    assert set(icons["services"]) == {"all_on", "all_off"}

    for entity_id in ("light.living_room", "light.kitchen"):
        state = hass.states.get(entity_id)
        assert state is not None
        assert ATTR_ICON not in state.attributes


# ---- State updates via events ----


async def test_zone_properties_changed(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test entity state updates on zone property change events."""
    hub = setup_lights

    # Simulate the hub pushing a property change.
    await hub.emit(
        f"{hub.EVENT_ZONE_PROPERTIES_CHANGED}:1",
        {
            hub.SERVICE: "ZonePropertiesChanged",
            hub.ZID: 1,
            hub.PROPERTY_LIST: {hub.POWER: True},
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("light.kitchen")
    assert state is not None
    assert state.state == STATE_ON


async def test_dimmer_brightness_update(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test dimmer brightness updates on zone property change."""
    hub = setup_lights

    await hub.emit(
        f"{hub.EVENT_ZONE_PROPERTIES_CHANGED}:0",
        {
            hub.SERVICE: "ZonePropertiesChanged",
            hub.ZID: 0,
            hub.PROPERTY_LIST: {hub.POWER_LEVEL: 50, hub.POWER: True},
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("light.living_room")
    assert state is not None
    # 50 / 100 * 255 = 127
    assert state.attributes[ATTR_BRIGHTNESS] == 127


# ---- Commands ----


async def test_switch_turn_on(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test turning on a switch."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.kitchen"},
        blocking=True,
    )
    # No exception means success; the mock hub returned Success.


async def test_switch_turn_off(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test turning off a switch."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "light.kitchen"},
        blocking=True,
    )


async def test_dimmer_turn_on_with_brightness(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test turning on a dimmer with brightness."""
    await hass.services.async_call(
        "light",
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "light.living_room", ATTR_BRIGHTNESS: 128},
        blocking=True,
    )


async def test_command_error_raises_ha_error(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test that a hub error response raises HomeAssistantError."""
    hub = setup_lights
    hub._set_zone_response = {
        "Status": "Error",
        "ErrorCode": "99",
        "ErrorText": "Zone busy",
    }

    with pytest.raises(HomeAssistantError, match="Zone busy"):
        await hass.services.async_call(
            "light",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "light.kitchen"},
            blocking=True,
        )


async def test_switch_command_timeout(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test that a switch command without a hub reply times out."""
    hub = setup_lights
    hub._set_zone_response = NO_REPLY

    with (
        patch(f"{LIGHT_PATCH}.COMMAND_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError, match="in time") as exc_info,
    ):
        await hass.services.async_call(
            "light",
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "light.kitchen"},
            blocking=True,
        )

    assert exc_info.value.translation_key == "command_timeout"
    assert exc_info.value.translation_placeholders == {"entity": "light.kitchen"}


async def test_dimmer_command_timeout(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test that a dimmer command without a hub reply times out."""
    hub = setup_lights
    hub._set_zone_response = NO_REPLY

    with (
        patch(f"{LIGHT_PATCH}.COMMAND_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError, match="in time") as exc_info,
    ):
        await hass.services.async_call(
            "light",
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: "light.living_room", ATTR_BRIGHTNESS: 128},
            blocking=True,
        )

    assert exc_info.value.translation_key == "command_timeout"
    assert exc_info.value.translation_placeholders == {
        "entity": "light.living_room"
    }


# ---- Availability ----


async def test_availability_tracks_connection(
    hass: HomeAssistant, setup_lights: MockHub
) -> None:
    """Test entity becomes unavailable when hub disconnects."""
    hub = setup_lights

    hub.connected = False
    await hub.emit(hub.EVENT_DISCONNECTED)
    await hass.async_block_till_done()

    state = hass.states.get("light.living_room")
    assert state is not None
    assert state.state == "unavailable"

    hub.connected = True
    hub.authenticated = True
    await hub.emit(hub.EVENT_AUTHENTICATED, MOCK_HOST)
    await hass.async_block_till_done()

    state = hass.states.get("light.living_room")
    assert state is not None
    assert state.state != "unavailable"


# ---- Brightness normalization ----


async def test_brightness_normalization() -> None:
    """Test brightness conversion between HA (0-255) and LC7001 (0-100)."""
    from custom_components.legrand_rflc.light import _Dimmer

    # LC7001 0 → HA 0
    assert _Dimmer._to_ha(0) == 0
    # LC7001 100 → HA 255
    assert _Dimmer._to_ha(100) == 255
    # LC7001 50 → HA 127
    assert _Dimmer._to_ha(50) == 127

    # HA 0 → LC7001 0
    assert _Dimmer._from_ha(0) == 0
    # HA 255 → LC7001 100
    assert _Dimmer._from_ha(255) == 100
    # HA 127 → LC7001 49 (truncation)
    assert _Dimmer._from_ha(127) == 49
