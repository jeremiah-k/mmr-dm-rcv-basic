# MMR Receive DMs Basic Plugin

A simple plugin for Meshtastic-Matrix-Relay that captures direct messages sent to the relay node and forwards them to a configured Matrix room.

## What It Does

- Receives direct messages sent to your relay node
- Forwards them to a Matrix room for visibility
- Includes sender information in forwarded messages
- Requires minimal configuration

## Configuration

Add this plugin to your MMR `config.yaml`:

```yaml
community-plugins:
  dm-rcv-basic:
    active: true
    repository: https://github.com/jeremiah-k/mmr-dm-rcv-basic.git
    branch: main
    dm_room: "!your-room-id:matrix.org" # Required: Matrix room for DMs
    dm_prefix: true # Optional: Show [DM] prefix (default: true)
```

Messages are formatted as: `[DM] Longname (DeviceID): Message`

## Usage

1. Add the plugin configuration to your MMR config
2. Restart MMR
3. Direct messages sent to your relay node will appear in the configured Matrix room

That's it! The plugin handles everything else automatically.
