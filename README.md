# HassBroadcastExtended
Extend Home Assistant Intent HassBroadcast to also broadcast to Media Players like Alexa and Music Assistant players in the home. This can be used when transferring from Alexa or Google Home to Home Assistant Voice.

## Usage

Add the integration to `configuration.yaml`:

```yaml
hass_broadcast_extended:
  tts_entity: tts.home_assistant_cloud
  exclude:
    - media_player.tv
```

If `include` is omitted, `HassBroadcast` targets every available `media_player` entity except anything listed in `exclude`. Set `include` to explicitly choose the players that should receive broadcasts. The integration overwrites the built-in `HassBroadcast` handler so existing Assist sentences keep working.
