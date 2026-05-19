# HassBroadcastExtended
Extend Home Assistant Intent HassBroadcast to also broadcast to Media Players like Alexa and Music Assistant players in the home. This can be used when transferring from Alexa or Google Home to Home Assistant Voice.

## Installation

### HACS

Hass Broadcast Extended can be installed with HACS as a custom repository:

1. Open HACS in Home Assistant.
2. Go to the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/mano3m/HassBroadcastExtended` as an **Integration** repository.
4. Install **Hass Broadcast Extended**.
5. Restart Home Assistant.

### Manual

Copy `custom_components/hass_broadcast_extended` into your Home Assistant `custom_components` directory, then restart Home Assistant.

## Configuration

Add the integration to `configuration.yaml`:

```yaml
hass_broadcast_extended:
  tts_entity: tts.home_assistant_cloud
  exclude:
    - media_player.tv
```

The integration overwrites the built-in `HassBroadcast` handler so existing Assist sentences keep working. It still calls the original handler first, then sends the same announcement to the configured media players.

### Options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `tts_entity` | No | Home Assistant's default TTS engine | TTS entity used for announcements, for example `tts.home_assistant_cloud`. |
| `include` | No | All media players | Explicit list of `media_player` entities that should receive broadcasts. |
| `exclude` | No | `[]` | List of `media_player` entities to skip. This is useful when `include` is omitted. |
| `cache` | No | `true` | Passed to the TTS `speak` service to control whether generated audio may be cached. |
| `language` | No | TTS engine default | Language code passed to the TTS `speak` service. |
| `options` | No | TTS engine default | Additional options passed directly to the TTS `speak` service. |
| `require_available` | No | `true` | When enabled, skips media players that are `unknown`, `unavailable`, or missing from Home Assistant state. |

If `include` is omitted, `HassBroadcast` targets every available `media_player` entity except anything listed in `exclude`. Set `include` to explicitly choose the players that should receive broadcasts.

For homes where the same physical speaker appears as multiple Home Assistant entities, such as HEOS, Alexa Media Player, and Music Assistant entities for one Denon device, prefer `include` and list only one entity per physical speaker.

Full example:

```yaml
hass_broadcast_extended:
  tts_entity: tts.home_assistant_cloud
  include:
    - media_player.kitchen_speaker
    - media_player.living_room_speaker
  exclude:
    - media_player.living_room_alexa_proxy
  cache: true
  language: en-US
  options:
    voice: JennyNeural
  require_available: true
```
