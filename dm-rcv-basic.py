"""
Basic Direct Message Receiver Plugin for MMRelay.

Captures direct messages sent to the relay node and forwards them
to a configured Matrix room for visibility and logging.

This is a simple, focused plugin that solves the core issue:
direct messages to the relay node are otherwise invisible to the relay operator.

Configuration:
    dm_room: Matrix room ID/alias where DMs should be forwarded (required)
    dm_prefix: Show [DM] prefix in messages (optional, default: true)

Example:
    community-plugins:
      dm-rcv-basic:
        active: true
        repository: https://github.com/jeremiah-k/mmr-dm-rcv-basic.git
        branch: main
        dm_room: "!dm-room:matrix.org"
        dm_prefix: true
"""

from mmrelay.db_utils import get_longname
from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    """Basic Direct Message Receiver Plugin for Meshtastic-Matrix-relay.

    Captures direct messages sent to the relay node and forwards them
    to a configured Matrix room for visibility and logging.

    This solves the issue where direct messages to the relay node
    are otherwise invisible to the relay operator.

    Configuration:
        dm_room: Matrix room ID/alias where DMs should be forwarded (required)
        dm_prefix: Show [DM] prefix in messages (optional, default: true)

    Features:
        - Automatic DM detection using BasePlugin.is_direct_message()
        - Forwarding to configured Matrix room
        - Sender information included in forwarded messages
        - Comprehensive logging for debugging
        - Minimal dependencies and configuration
    """

    plugin_name = "dm-rcv-basic"

    @property
    def description(self):
        """Get plugin description for help system."""
        return "Forward direct messages to Matrix room for visibility"

    def __init__(self):
        """Initialize the direct message plugin."""
        super().__init__()

        # Validate required configuration
        self.dm_room = self.config.get("dm_room")
        if not self.dm_room:
            self.logger.error("dm-rcv-basic plugin requires 'dm_room' configuration")
            raise ValueError("Missing required 'dm_room' configuration")

        # Optional configuration
        self.dm_prefix = self.config.get("dm_prefix", True)

        self.logger.info(
            f"Direct message plugin initialized - forwarding DMs to room: {self.dm_room}"
        )

    async def handle_meshtastic_message(
        self, packet, _formatted_message, longname, _meshnet_name
    ):
        """Handle incoming Meshtastic messages and process direct messages."""

        # Check if this is a direct message
        if not self.is_direct_message(packet):
            return False

        # Extract message content
        if "decoded" not in packet or "text" not in packet["decoded"]:
            self.logger.debug("Received non-text DM packet, ignoring")
            return False

        message_text = packet["decoded"]["text"].strip()
        sender_id = packet.get("fromId")

        if not message_text:
            self.logger.debug("Received empty DM, ignoring")
            return False

        # Get sender information
        sender_longname = longname or get_longname(sender_id) or str(sender_id)

        self.logger.info(
            f"Received DM from {sender_longname}: {message_text[:50]}{'...' if len(message_text) > 50 else ''}"
        )

        # Forward to Matrix room
        await self._forward_to_matrix(
            sender_longname, sender_id, message_text
        )

        return True  # Indicate we handled this message

    async def handle_room_message(self, room, event, full_message):
        """Handle Matrix commands - none needed for basic version."""

        # This basic version doesn't handle Matrix commands
        # It only forwards incoming DMs to the room
        return False

    def get_matrix_commands(self):
        """Get list of Matrix commands this plugin responds to."""
        return []  # No commands in basic version

    async def _forward_to_matrix(
        self, sender_longname, sender_id, message_text
    ):
        """Forward direct message to the configured Matrix room."""
        try:
            # Build prefix
            prefix = "[DM] " if self.dm_prefix else ""

            # Format the message for Matrix
            formatted_message = (
                f"{prefix}{sender_longname} ({sender_id}): {message_text}"
            )

            await self.send_matrix_message(self.dm_room, formatted_message)
            self.logger.info(f"Forwarded DM to Matrix room {self.dm_room}")

        except Exception:
            # Catch all exceptions to ensure plugin doesn't crash the relay
            # send_matrix_message may raise various exceptions (network, API, etc.)
            # Using broad exception handling for robustness in a plugin context
            self.logger.exception("Failed to forward DM to Matrix")
