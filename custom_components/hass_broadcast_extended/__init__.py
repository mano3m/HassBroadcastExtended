"""Extend HassBroadcast to announce on media players."""

from __future__ import annotations

import logging
from pathlib import Path
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
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent
from homeassistant.helpers import start as ha_start
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
_SENTENCE_FILE_HEADER = "# Managed by Hass Broadcast Extended. Do not edit.\n"
_SENTENCE_FILES = {
    ("nl", "hass_broadcast_extended.yaml"): Path(__file__).with_name("sentences")
    / "nl"
    / "homeassistant_HassBroadcast.yaml",
}

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

    updated_sentence_languages = await hass.async_add_executor_job(
        _install_sentence_files, hass
    )
    for language in updated_sentence_languages:
        if hass.services.has_service("conversation", "reload"):
            await hass.services.async_call(
                "conversation",
                "reload",
                {"language": language},
                blocking=False,
            )

    @callback
    def register_handler(_: HomeAssistant) -> None:
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
        _LOGGER.debug(
            "Registered Hass Broadcast Extended handler; original handler: %s",
            original_handler,
        )

    ha_start.async_at_started(hass, register_handler)
    return True


def _install_sentence_files(hass: HomeAssistant) -> set[str]:
    """Install bundled custom sentences into Home Assistant's sentence directory."""
    updated_languages: set[str] = set()
    for (language, filename), source_path in _SENTENCE_FILES.items():
        target_path = Path(hass.config.path("custom_sentences", language, filename))
        source_text = source_path.read_text(encoding="utf-8")
        target_text = f"{_SENTENCE_FILE_HEADER}{source_text}"

        if target_path.exists():
            existing_text = target_path.read_text(encoding="utf-8")
            if existing_text == target_text:
                continue
            if not existing_text.startswith(_SENTENCE_FILE_HEADER):
                _LOGGER.warning(
                    "Skipping custom sentence file because it is not managed by "
                    "Hass Broadcast Extended: %s",
                    target_path,
                )
                continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(target_text, encoding="utf-8")
        updated_languages.add(language)
        _LOGGER.debug("Installed custom sentence file: %s", target_path)

    return updated_languages


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

        _LOGGER.debug("Handling HassBroadcast message with %d characters", len(message))

        original_response = (
            await self.original_handler.async_handle(intent_obj)
            if self.original_handler
            else None
        )
        if original_response:
            _LOGGER.debug(
                "Original broadcast intent returned successful targets: %s",
                self._format_response_targets(original_response.success_results),
            )
        else:
            _LOGGER.debug("No original broadcast intent handler was available")

        original_entity_ids = {
            target.id
            for target in (
                original_response.success_results if original_response else []
            )
            if target.id is not None
        }

        media_player_entity_ids = self._get_media_player_entity_ids(
            intent_obj.hass, original_entity_ids
        )
        if not media_player_entity_ids:
            _LOGGER.debug("No additional media player targets selected for broadcast")
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

        _LOGGER.debug(
            "Calling %s.%s using %s for media player targets: %s",
            TTS_DOMAIN,
            SERVICE_SPEAK,
            tts_entity_id,
            media_player_entity_ids,
        )
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
            "Queued HassBroadcast media player announcement for successful targets: %s",
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
            state = hass.states.get(entity_id)
            supported_features = (
                state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) if state else 0
            )

            if entity_id in handled_entity_ids:
                reason = "it was already handled by the original broadcast intent"
            elif entity_id in exclude:
                reason = "it is excluded by configuration"
            elif require_available and (
                state is None or state.state in _UNUSABLE_STATES
            ):
                reason = "it is not available"
            elif not supported_features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE:
                reason = "it does not support announcements"
            else:
                targets.append(entity_id)
                continue

            _LOGGER.debug("Skipping media player %s because %s", entity_id, reason)

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
