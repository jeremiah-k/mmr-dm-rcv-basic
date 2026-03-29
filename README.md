# MMR DM Receiver Basic Plugin

A simple plugin for Meshtastic-Matrix-Relay that captures direct messages sent to the relay node and forwards them to a configured Matrix room.

## What It Does

- Receives direct messages sent to your relay node
- Forwards them to a Matrix room for visibility
- Includes sender information in forwarded messages
- Requires minimal configuration

## Quick Setup

Add this to your MMR `config.yaml`:

```yaml
community-plugins:
  dm-rcv-basic:
    active: true
    repository: https://github.com/jeremiah-k/mmr-dm-rcv-basic.git
    branch: main
    dm_room: "!your-room-id:matrix.org" # Required: Matrix room for DMs
    dm_prefix: true # Optional: Show [DM] prefix (default: true)
```

**That's it!** Restart MMR and the plugin will automatically forward direct messages to your configured room.

## Message Format

Messages appear as: `[DM] Longname (DeviceID): Message`

## Requirements

- MMRelay version 1.2.5 or later
- A Matrix room ID where DMs should be forwarded

