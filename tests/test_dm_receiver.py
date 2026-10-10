"""Exercise room joining against the MMRelay plugin API."""

import asyncio
import importlib.util
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mmrelay import matrix_utils
from mmrelay.plugins import base_plugin
from nio import RoomSendError, RoomSendResponse

ROOM = "!inbox:example.org"
SPEC = importlib.util.spec_from_file_location(
    "dm_receiver", Path(__file__).parents[1] / "dm-rcv-basic.py"
)
receiver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = receiver
SPEC.loader.exec_module(receiver)


@pytest.fixture
def plugin(monkeypatch):
    """Use real BasePlugin initialization with isolated configuration and logging."""
    monkeypatch.setattr(
        base_plugin,
        "config",
        {"community-plugins": {"dm-rcv-basic": {"active": True, "dm_room": ROOM}}},
    )
    monkeypatch.setattr(base_plugin, "get_logger", logging.getLogger)
    return receiver.Plugin()


def client(rooms=None):
    """Provide a Matrix client whose sync event is already set."""
    synced = asyncio.Event()
    synced.set()
    return SimpleNamespace(
        rooms={} if rooms is None else rooms,
        synced=synced,
        join=AsyncMock(return_value=SimpleNamespace(room_id=ROOM)),
        room_resolve_alias=AsyncMock(return_value=SimpleNamespace(room_id=ROOM)),
    )


def test_join_waits_for_room_state_after_an_earlier_sync(plugin, monkeypatch):
    """A set sync event must not exhaust the wait before room state arrives."""

    async def scenario():
        matrix = client()

        async def join(room_id):
            asyncio.get_running_loop().call_later(
                0.01, matrix.rooms.update, {ROOM: object()}
            )
            return SimpleNamespace(room_id=room_id)

        matrix.join.side_effect = join
        monkeypatch.setattr(receiver, "connect_matrix", AsyncMock(return_value=matrix))
        assert await plugin._ensure_joined() is True
        matrix.join.assert_awaited_once_with(ROOM)
        assert matrix.synced.is_set()

    asyncio.run(scenario())


def test_recreated_client_rejoins_room(plugin, monkeypatch):
    """Cached membership from another client must not bypass joining."""

    async def scenario():
        first = client({ROOM: object()})
        second = client()

        async def join(room_id):
            second.rooms[room_id] = object()
            return SimpleNamespace(room_id=room_id)

        second.join.side_effect = join
        monkeypatch.setattr(
            receiver, "connect_matrix", AsyncMock(side_effect=[first, second])
        )
        assert await plugin._ensure_joined() is True
        assert await plugin._ensure_joined() is True
        second.join.assert_awaited_once_with(ROOM)

    asyncio.run(scenario())


def test_concurrent_forwards_share_a_join(plugin, monkeypatch):
    """Serialize joining while allowing both forwards to observe room state."""

    async def scenario():
        matrix = client()

        async def join(room_id):
            await asyncio.sleep(0)
            matrix.rooms[room_id] = object()
            return SimpleNamespace(room_id=room_id)

        matrix.join.side_effect = join
        monkeypatch.setattr(receiver, "connect_matrix", AsyncMock(return_value=matrix))
        assert await asyncio.gather(
            plugin._ensure_joined(), plugin._ensure_joined()
        ) == [
            True,
            True,
        ]
        matrix.join.assert_awaited_once_with(ROOM)

    asyncio.run(scenario())


def test_room_sync_timeout_is_bounded_and_retryable(plugin, monkeypatch):
    """Missing room state refuses delivery without caching a successful join."""

    async def scenario():
        matrix = client()
        monkeypatch.setattr(receiver, "_ROOM_SYNC_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(receiver, "connect_matrix", AsyncMock(return_value=matrix))
        assert await asyncio.wait_for(plugin._ensure_joined(), timeout=1) is False
        matrix.rooms[ROOM] = object()
        assert await plugin._ensure_joined() is True

    asyncio.run(scenario())


def test_alias_resolution_registers_the_internal_room_id(plugin, monkeypatch):
    """Room ownership follows alias resolution without requiring matrix_rooms."""
    plugin.dm_room = "#inbox:example.org"
    matrix = client({ROOM: object()})
    monkeypatch.setattr(receiver, "connect_matrix", AsyncMock(return_value=matrix))
    assert asyncio.run(plugin._ensure_joined()) is True
    assert plugin.handles_unmapped_rooms is True
    assert plugin.get_unmapped_room_ids() == [ROOM]
    matrix.join.assert_not_awaited()


def test_inbox_ownership_does_not_expose_matrix_commands(plugin):
    """Declare the configured inbox before a first DM without adding commands."""
    assert plugin.handles_unmapped_rooms is True
    assert plugin.get_unmapped_room_ids() == [ROOM]
    assert plugin.get_matrix_commands() == []


@pytest.mark.parametrize("room", [None, True, 123, "", "room", "!:server", "!room:"])
def test_invalid_inbox_configuration_is_rejected(plugin, room):
    """Reject room values that cannot identify a Matrix room or alias."""
    plugin.config["dm_room"] = room
    with pytest.raises(ValueError, match="dm_room"):
        receiver.Plugin()


@pytest.mark.parametrize("failure", ["no_client", "join_error", "alias_error"])
def test_join_failures_refuse_delivery(plugin, monkeypatch, failure):
    """Connection, join and alias failures leave the room unavailable."""
    matrix = client()
    if failure == "no_client":
        matrix = None
    elif failure == "join_error":
        matrix.join.return_value = SimpleNamespace(message="Forbidden")
    else:
        plugin.dm_room = "#inbox:example.org"
        matrix.room_resolve_alias.return_value = SimpleNamespace(message="Not found")
    monkeypatch.setattr(receiver, "connect_matrix", AsyncMock(return_value=matrix))
    assert asyncio.run(plugin._ensure_joined()) is False


def test_join_cancellation_propagates_and_releases_lock(plugin, monkeypatch):
    """Relay shutdown must cancel a pending join and permit a later retry."""

    async def scenario():
        matrix = client()
        matrix.join.side_effect = asyncio.CancelledError
        monkeypatch.setattr(receiver, "connect_matrix", AsyncMock(return_value=matrix))
        with pytest.raises(asyncio.CancelledError):
            await plugin._ensure_joined()
        assert not plugin._join_lock.locked()
        matrix.rooms[ROOM] = object()
        assert await plugin._ensure_joined() is True

    asyncio.run(scenario())


@pytest.fixture
def delivery(plugin, monkeypatch):
    """Keep the real DM detector and Matrix sender while replacing network I/O."""
    plugin._my_node_id = 0x12345678
    matrix = client({ROOM: object()})
    matrix.room_send = AsyncMock(return_value=RoomSendResponse("$event", ROOM))
    connect = AsyncMock(return_value=matrix)
    monkeypatch.setattr(receiver, "connect_matrix", connect)
    monkeypatch.setattr(matrix_utils, "connect_matrix", connect)
    return matrix


def packet(**overrides):
    """Build a direct text packet with the fields supplied by Meshtastic."""
    value = {
        "to": 0x12345678,
        "from": 0xA4BF9D0C,
        "fromId": "!a4bf9d0c",
        "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "hello"},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("prefix", [True, False])
def test_dm_body_is_literal_and_blocking_helpers_run_off_loop(
    plugin, delivery, monkeypatch, caplog, prefix
):
    """Preserve untrusted text and keep node/database lookups off the event loop."""
    loop_thread = threading.get_ident()
    text = "  **private** <b>message</b>\nsecond line  "
    name = "Sender **name**"

    def get_node_id():
        assert threading.get_ident() != loop_thread
        return 0x12345678

    def get_name(sender_id):
        assert threading.get_ident() != loop_thread
        assert sender_id == "!a4bf9d0c"
        return name

    monkeypatch.setattr(plugin, "get_my_node_id", get_node_id)
    monkeypatch.setattr(receiver, "get_longname", get_name)
    plugin.dm_prefix = prefix
    with caplog.at_level(logging.INFO):
        assert (
            asyncio.run(
                plugin.handle_meshtastic_message(
                    packet(decoded={"text": text}), "unused", None, "unused"
                )
            )
            is True
        )
    delivery.room_send.assert_awaited_once_with(
        room_id=ROOM,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": f"{'[DM] ' if prefix else ''}{name} (!a4bf9d0c): {text}",
        },
    )
    assert "private" not in caplog.text
    assert "Forwarded DM" in caplog.text


@pytest.mark.parametrize("destination", [None, 0xFFFFFFFF, 0x87654321])
def test_packets_not_addressed_to_relay_are_ignored(plugin, delivery, destination):
    """Other recipients and broadcasts must never enter the private inbox."""
    assert (
        asyncio.run(
            plugin.handle_meshtastic_message(packet(to=destination), "", "Sender", "")
        )
        is False
    )
    delivery.room_send.assert_not_awaited()


@pytest.mark.parametrize(
    "incoming",
    [
        None,
        {},
        packet(decoded=None),
        packet(decoded={}),
        packet(decoded={"text": None}),
        packet(decoded={"text": 123}),
        packet(decoded={"text": b"binary"}),
        packet(decoded={"text": " \n "}),
    ],
)
def test_malformed_or_empty_text_is_ignored(plugin, delivery, incoming):
    """Malformed decoder output must not raise or send content to Matrix."""
    assert (
        asyncio.run(plugin.handle_meshtastic_message(incoming, "", "Sender", ""))
        is False
    )
    delivery.room_send.assert_not_awaited()


def test_numeric_sender_is_normalized_for_lookup_and_display(
    plugin, delivery, monkeypatch
):
    """Packets without fromId retain an identifiable sender from the node number."""
    get_name = Mock(return_value=None)
    monkeypatch.setattr(receiver, "get_longname", get_name)
    assert (
        asyncio.run(plugin.handle_meshtastic_message(packet(fromId=None), "", None, ""))
        is True
    )
    get_name.assert_called_once_with("!a4bf9d0c")
    assert delivery.room_send.call_args.kwargs["content"]["body"] == (
        "[DM] !a4bf9d0c (!a4bf9d0c): hello"
    )


@pytest.mark.parametrize(
    "failure", [None, RoomSendError("Forbidden"), RuntimeError("offline")]
)
def test_failed_matrix_sends_are_not_reported_as_delivered(
    plugin, delivery, caplog, failure
):
    """A claimed DM may fail delivery without emitting a success log."""
    if isinstance(failure, Exception):
        delivery.room_send.side_effect = failure
    else:
        delivery.room_send.return_value = failure
    with caplog.at_level(logging.INFO):
        assert (
            asyncio.run(plugin.handle_meshtastic_message(packet(), "", "Sender", ""))
            is True
        )
    assert "Forwarded DM" not in caplog.text
    assert "Failed to forward DM" in caplog.text


def test_delivery_cancellation_propagates(plugin, delivery):
    """Do not suppress relay shutdown while sending a DM."""
    delivery.room_send.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin.handle_meshtastic_message(packet(), "", "Sender", ""))
