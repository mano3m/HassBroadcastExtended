"""Extend HassBroadcast to announce on media players."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from homeassistant.components.media_player.const import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
)
from homeassistant.components.tts import (
    ATTR_MEDIA_PLAYER_ENTITY_ID,
    async_default_engine,
)
from homeassistant.components.tts.const import (
    ATTR_CACHE,
    ATTR_LANGUAGE,
    ATTR_MESSAGE,
    ATTR_OPTIONS,
)
from homeassistant.components.tts.const import (
    DOMAIN as TTS_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    CONF_CACHE,
    CONF_EXCLUDE,
    CONF_INCLUDE,
    CONF_LANGUAGE,
    CONF_OPTIONS,
    CONF_REQUIRE_AVAILABLE,
    CONF_TTS_ENTITY,
    DEFAULT_CACHE,
    DEFAULT_REQUIRE_AVAILABLE,
    DOMAIN,
    SERVICE_SPEAK,
)

_LOGGER = logging.getLogger(__name__)

_UNUSABLE_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN}

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_TTS_ENTITY): cv.entity_domain(TTS_DOMAIN),
                vol.Optional(CONF_INCLUDE, default=[]): vol.All(
                    cv.ensure_list, [cv.entity_domain(MEDIA_PLAYER_DOMAIN)]
                ),
                vol.Optional(CONF_EXCLUDE, default=[]): vol.All(
                    cv.ensure_list, [cv.entity_domain(MEDIA_PLAYER_DOMAIN)]
                ),
                vol.Optional(CONF_CACHE, default=DEFAULT_CACHE): cv.boolean,
                vol.Optional(CONF_LANGUAGE): cv.string,
                vol.Optional(CONF_OPTIONS): dict,
                vol.Optional(
                    CONF_REQUIRE_AVAILABLE, default=DEFAULT_REQUIRE_AVAILABLE
                ): cv.boolean,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Hass Broadcast Extended."""
    conf: dict[str, Any] = dict(config.get(DOMAIN, {}))

    @callback
    def register_handler(_: Event | None = None) -> None:
        current_handler = hass.data.get(intent.DATA_KEY, {}).get(
            intent.INTENT_BROADCAST
        )
        original_handler = (
            current_handler.original_handler
            if isinstance(current_handler, BroadcastToMediaPlayersIntentHandler)
            else current_handler
        )
        intent.async_register(
            hass, BroadcastToMediaPlayersIntentHandler(conf, original_handler)
        )

    register_handler()
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, register_handler)
    return True


class BroadcastToMediaPlayersIntentHandler(intent.IntentHandler):
    """Broadcast messages to media players instead of only Assist satellites."""

    intent_type = intent.INTENT_BROADCAST
    description = "Announces a message on media players"
    slot_schema: ClassVar[dict[Any, Any]] = {
        vol.Required("message"): intent.non_empty_string
    }

    def __init__(
        self,
        config: dict[str, Any],
        original_handler: intent.IntentHandler | None,
    ) -> None:
        """Initialize the intent handler."""
        self._config = config
        self.original_handler = original_handler

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle HassBroadcast."""
        slots = self.async_validate_slots(intent_obj.slots)
        message: str = slots["message"]["value"]

        original_response = await self._async_handle_original_intent(intent_obj)
        if original_response:
            _LOGGER.debug(
                "Original broadcast intent returned successful targets: %s",
                self._format_response_targets(original_response.success_results),
            )
        else:
            _LOGGER.debug("No original broadcast intent handler was available")

        original_entity_ids: set[str] = (
            {
                target.id
                for target in original_response.success_results
                if target.id is not None
            }
            if original_response
            else set()
        )

        media_player_entity_ids = self._get_media_player_entity_ids(
            intent_obj.hass, original_entity_ids
        )
        if not media_player_entity_ids:
            if original_response:
                return original_response
            raise intent.IntentHandleError("No media players available for broadcast")

        tts_entity_id = self._config.get(CONF_TTS_ENTITY) or async_default_engine(
            intent_obj.hass
        )
        if not tts_entity_id:
            raise intent.IntentHandleError("No text-to-speech engine is available")

        service_data: dict[str, Any] = {
            ATTR_MEDIA_PLAYER_ENTITY_ID: media_player_entity_ids,
            ATTR_MESSAGE: message,
            ATTR_CACHE: self._config[CONF_CACHE],
        }

        if language := self._config.get(CONF_LANGUAGE):
            service_data[ATTR_LANGUAGE] = language

        if options := self._config.get(CONF_OPTIONS):
            service_data[ATTR_OPTIONS] = options

        await intent_obj.hass.services.async_call(
            TTS_DOMAIN,
            SERVICE_SPEAK,
            service_data,
            blocking=False,
            context=intent_obj.context,
            target={ATTR_ENTITY_ID: tts_entity_id},
        )

        media_player_response_targets = self._get_media_player_response_targets(
            intent_obj.hass, media_player_entity_ids
        )
        _LOGGER.debug(
            "Hass Broadcast Extended returned successful media player targets: %s",
            self._format_response_targets(media_player_response_targets),
        )

        response = intent_obj.create_response()
        response.async_set_results(
            success_results=[
                *(original_response.success_results if original_response else []),
                *media_player_response_targets,
            ],
            failed_results=original_response.failed_results
            if original_response
            else None,
        )
        return response

    async def _async_handle_original_intent(
        self, intent_obj: intent.Intent
    ) -> intent.IntentResponse | None:
        """Run the original HassBroadcast handler, if one was registered."""
        if self.original_handler is None:
            return None

        return await self.original_handler.async_handle(intent_obj)

    def _get_media_player_entity_ids(
        self, hass: HomeAssistant, handled_entity_ids: set[str]
    ) -> list[str]:
        """Return media players targeted by this integration."""
        include: list[str] = self._config[CONF_INCLUDE]
        exclude: set[str] = set(self._config[CONF_EXCLUDE])
        require_available: bool = self._config[CONF_REQUIRE_AVAILABLE]

        if include:
            entity_ids = include
        else:
            entity_ids = [
                state.entity_id for state in hass.states.async_all(MEDIA_PLAYER_DOMAIN)
            ]

        targets: list[str] = []
        for entity_id in entity_ids:
            if entity_id in handled_entity_ids:
                _LOGGER.debug(
                    "Skipping media player %s because it was already handled by "
                    "the original broadcast intent",
                    entity_id,
                )
                continue

            if entity_id in exclude:
                _LOGGER.debug(
                    "Skipping media player %s because it is excluded by configuration",
                    entity_id,
                )
                continue

            state = hass.states.get(entity_id)
            if require_available and (state is None or state.state in _UNUSABLE_STATES):
                _LOGGER.debug(
                    "Skipping media player %s because it is not available",
                    entity_id,
                )
                continue

            supported_features = (
                state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state else 0
            )
            if not supported_features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE:
                _LOGGER.debug(
                    "Skipping media player %s because it does not support announcements",
                    entity_id,
                )
                continue

            targets.append(entity_id)

        return targets

    def _get_media_player_response_targets(
        self, hass: HomeAssistant, entity_ids: list[str]
    ) -> list[intent.IntentResponseTarget]:
        """Return response targets for media players."""
        return [
            intent.IntentResponseTarget(
                type=intent.IntentResponseTargetType.ENTITY,
                id=entity_id,
                name=state.name if (state := hass.states.get(entity_id)) else entity_id,
            )
            for entity_id in entity_ids
        ]

    @staticmethod
    def _format_response_targets(
        targets: list[intent.IntentResponseTarget],
    ) -> list[str]:
        """Return readable target labels for debug logging."""
        return [
            f"{target.id} ({target.name})" if target.id else target.name
            for target in targets
        ]
