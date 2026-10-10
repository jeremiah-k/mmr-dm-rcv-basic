# MMR DM Receiver Basic Plugin

A simple plugin for Meshtastic-Matrix-Relay that captures direct messages sent to the relay node and forwards them to a configured Matrix room.

## What It Does

- Receives direct messages sent to your relay node
- Forwards them to a Matrix room for visibility
- Includes sender information in forwarded messages
- Requires minimal configuration

## Quick Setup

Add this to your MMR `config.yaml`. Replace `<full-commit-sha>` with the full
40-character SHA of the plugin commit you have reviewed:

```yaml
community-plugins:
  dm-rcv-basic:
    active: true
    repository: https://github.com/jeremiah-k/mmr-dm-rcv-basic.git
    commit: "<full-commit-sha>"
    dm_room: "!your-room-id:matrix.org" # Required: Matrix room for DMs
    dm_prefix: true # Optional: Show [DM] prefix (default: true)
```

Restart MMR after saving the configuration.

Invite the relay's Matrix account to the inbox room and give it permission to
send messages. Use an internal room ID (`!room:server`) when possible. Relays
that support plugin-owned rooms accept inbox invitations without a
`matrix_rooms` entry; room aliases depend on alias metadata in the invitation.
The plugin also resolves aliases and joins lazily when a DM arrives. Joining
waits up to 50 seconds for room state to reach the Matrix client. A failed join
skips that forward; a later DM retries. Membership is checked against the active
client on every forward, including after reconnects.

Use a private inbox with an audience appropriate for direct messages. Everyone
in that Matrix room can read the forwarded messages. Keep the inbox out of
`matrix_rooms` unless ordinary conversation there should also reach the mesh.
The plugin does not send Matrix replies back to the DM sender.

## Message Format

Messages appear as: `[DM] Longname (DeviceID): Message`

## Requirements

- MMRelay version 1.2.5 or later
- A Matrix room ID where DMs should be forwarded
