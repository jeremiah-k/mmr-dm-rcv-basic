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
        commit: "<full-commit-sha>"  # Replace with a reviewed commit SHA
        dm_room: "!dm-room:matrix.org"
        dm_prefix: true
"""

import asyncio
from collections.abc import Mapping

from mmrelay.db_utils import get_longname
from mmrelay.matrix_utils import connect_matrix
from mmrelay.plugins.base_plugin import BasePlugin
from nio import RoomSendError

_ROOM_SYNC_TIMEOUT_SECONDS = 50.0
_ROOM_SYNC_POLL_SECONDS = 0.1


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
    handles_unmapped_rooms = True

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
        dm_room = self.config.get("dm_room")
        if not isinstance(dm_room, str):
            raise ValueError("'dm_room' must be a Matrix room ID or alias")
        self.dm_room = dm_room.strip()
        localpart, separator, server = self.dm_room[1:].partition(":")
        if (
            not self.dm_room.startswith(("!", "#"))
            or not localpart
            or not separator
            or not server
        ):
            raise ValueError("'dm_room' must be a Matrix room ID or alias")

        # Optional configuration
        self.dm_prefix = self.config.get("dm_prefix", True)

        self.logger.info(
            f"Direct message plugin initialized - forwarding DMs to room: {self.dm_room}"
        )

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
            bool: True when a text DM is claimed, including a failed delivery;
                False for packets this plugin does not handle.
        """

        if not isinstance(packet, Mapping):
            return False
        decoded = packet.get("decoded")
        if not isinstance(decoded, Mapping):
            self.logger.debug("Received non-text DM packet, ignoring")
            return False
        message_text = decoded.get("text")
        if not isinstance(message_text, str) or not message_text.strip():
            self.logger.debug("Received empty or non-text packet, ignoring")
            return False
        if not await asyncio.to_thread(self.is_direct_message, packet):
            return False

        sender_id = packet.get("fromId")
        if not isinstance(sender_id, str) or not sender_id:
            sender_num = packet.get("from")
            sender_id = (
                f"!{sender_num:08x}"
                if type(sender_num) is int and 0 < sender_num < 0xFFFFFFFF
                else "unknown"
            )
        sender_longname = (
            longname or await asyncio.to_thread(get_longname, sender_id) or sender_id
        )

        self.logger.info(
            "Received text DM from %s (%d characters)", sender_id, len(message_text)
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

    def get_unmapped_room_ids(self) -> list[str]:
        """Declare the inbox for plugin-owned room invitation handling."""
        return [self.dm_room]

    async def _ensure_joined(self) -> bool:
        """
        Ensure the bot has joined the configured DM room.

        Returns:
            bool: True if the room is already joined or join succeeds; False otherwise.
        """
        async with self._join_lock:
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
                self.dm_room = target_room

            if target_room in matrix_client.rooms:
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
                    getattr(response, "message", response)
                    if response
                    else "Unknown error"
                )
                self.logger.error(
                    f"Failed to join DM room {target_room}: {error_details}"
                )
                return False

            # A client's synced event may still be set from an earlier sync.
            # Wait for this room's state without clearing that shared event.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _ROOM_SYNC_TIMEOUT_SECONDS
            while joined_room_id not in matrix_client.rooms:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(_ROOM_SYNC_POLL_SECONDS, remaining))

            if joined_room_id not in matrix_client.rooms:
                self.logger.error(
                    f"Joined DM room {joined_room_id}, but it never appeared in Matrix client rooms cache"
                )
                return False

            self.logger.info(f"Joined DM room {joined_room_id} successfully")
            self.dm_room = joined_room_id
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

            response = await self.send_matrix_message(
                self.dm_room, formatted_message, formatted=False
            )
            if response is None or isinstance(response, RoomSendError):
                self.logger.error(
                    "Failed to forward DM to Matrix room %s", self.dm_room
                )
                return
            self.logger.info(f"Forwarded DM to Matrix room {self.dm_room}")

        except Exception:
            # Catch all exceptions to ensure plugin doesn't crash the relay
            # send_matrix_message may raise various exceptions (network, API, etc.)
            # Using broad exception handling for robustness in a plugin context
            self.logger.exception("Failed to forward DM to Matrix")
