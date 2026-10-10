"""Exercise room joining against the MMRelay plugin API."""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mmrelay.plugins import base_plugin

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
