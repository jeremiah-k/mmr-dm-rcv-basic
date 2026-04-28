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
        commit: 5d4fa7d6011b6efc9590e2ee79295e87a5b349bc
        dm_room: "!dm-room:matrix.org"
        dm_prefix: true
"""

import asyncio

from mmrelay.db_utils import get_longname
from mmrelay.matrix_utils import connect_matrix
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
        """
        Configure and validate plugin settings required to forward direct messages to a Matrix room.

        Reads and stores the required `dm_room` configuration and the optional `dm_prefix` flag (defaults to True). Raises ValueError if `dm_room` is not provided. Logs initialization status.
        Raises:
            ValueError: If the required `dm_room` configuration is missing.
        """
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

        self._joined_room = False
        self._join_lock = asyncio.Lock()

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        """
        Process an incoming Meshtastic message and forward direct messages to the configured Matrix room.

        Parameters:
            packet (dict): Meshtastic message packet; expected to contain 'decoded' with 'text' for text messages and may include 'fromId'.
            formatted_message (str | None): Preformatted message text (not used by this handler).
            longname (str | None): Optional sender display name to use instead of resolving from the packet.
            meshnet_name (str | None): Mesh network name (not used by this handler).

        Returns:
            bool: `True` if the message was identified as a direct message and forwarded to the Matrix room, `False` otherwise.
        """

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
        await self._forward_to_matrix(sender_longname, sender_id, message_text)

        return True  # Indicate we handled this message

    async def handle_room_message(self, room, event, full_message):
        """Handle Matrix commands - none needed for basic version."""

        # This basic version doesn't handle Matrix commands
        # It only forwards incoming DMs to the room
        return False

    def get_matrix_commands(self):
        """
        List Matrix commands the plugin supports.

        Returns:
            commands (list): List of command descriptors accepted by the plugin; empty if the plugin exposes no Matrix commands.
        """
        return []  # No commands in basic version

    async def _ensure_joined(self) -> bool:
        """
        Ensure the bot has joined the configured DM room.

        Returns:
            bool: True if the room is already joined or join succeeds; False otherwise.
        """
        if self._joined_room:
            return True

        async with self._join_lock:
            if self._joined_room:
                return True

            matrix_client = await connect_matrix()
            if matrix_client is None:
                self.logger.error("Failed to connect to Matrix client for room join")
                return False

            target_room = self.dm_room
            if target_room.startswith("#"):
                try:
                    alias_response = await matrix_client.room_resolve_alias(target_room)
                except Exception:
                    self.logger.exception(
                        f"Error resolving DM room alias {target_room}"
                    )
                    return False

                resolved_room_id = (
                    getattr(alias_response, "room_id", None) if alias_response else None
                )
                if not resolved_room_id:
                    error_details = (
                        getattr(alias_response, "message", alias_response)
                        if alias_response
                        else "Unknown error"
                    )
                    self.logger.error(
                        f"Failed to resolve DM room alias {target_room}: {error_details}"
                    )
                    return False
                target_room = resolved_room_id

            if target_room in matrix_client.rooms:
                self._joined_room = True
                self.dm_room = target_room
                self.logger.debug(f"Already in DM room {target_room}")
                return True

            self.logger.info(f"Joining DM room {target_room}...")
            try:
                response = await matrix_client.join(target_room)
            except Exception:
                self.logger.exception(f"Error joining DM room {target_room}")
                return False

            joined_room_id = getattr(response, "room_id", None) if response else None
            if not joined_room_id:
                error_details = (
                    getattr(response, "message", response) if response else "Unknown error"
                )
                self.logger.error(
                    f"Failed to join DM room {target_room}: {error_details}"
                )
                return False

            if joined_room_id not in matrix_client.rooms:
                synced = getattr(matrix_client, "synced", None)
                if synced is None:
                    self.logger.error(
                        f"Joined DM room {joined_room_id}, but Matrix sync state is unavailable"
                    )
                    return False

                for _ in range(10):
                    if joined_room_id in matrix_client.rooms:
                        break
                    try:
                        await asyncio.wait_for(synced.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        self.logger.debug(
                            f"Waiting for DM room {joined_room_id} to appear in client cache..."
                        )

            if joined_room_id not in matrix_client.rooms:
                self.logger.error(
                    f"Joined DM room {joined_room_id}, but it never appeared in Matrix client rooms cache"
                )
                return False

            self.logger.info(f"Joined DM room {joined_room_id} successfully")
            self.dm_room = joined_room_id
            self._joined_room = True
            return True

    async def _forward_to_matrix(self, sender_longname, sender_id, message_text):
        """
        Send a direct Meshtastic message to the configured Matrix room.

        Formats the message (optionally prefixed with "[DM]") to include the sender's display name and node ID, then delivers it to the plugin's configured Matrix room.

        Parameters:
            sender_longname (str): Human-readable name for the sender (may be a resolved longname or a fallback ID string).
            sender_id (str): Sender's Mesh node identifier.
            message_text (str): Raw message text to forward.
        """
        try:
            if not await self._ensure_joined():
                self.logger.warning(
                    f"Skipping DM forward because room join failed for {self.dm_room}"
                )
                return

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
