"""Update-loop pacing: which commands each pass reads, and when."""

import asyncio
from unittest.mock import MagicMock

import pytest

from arcam.fmj.client import Client
from arcam.fmj.codecs import SourceCodes
from arcam.fmj.commands import CommandCodes
from arcam.fmj.errors import CommandInvalidAtThisTime
from arcam.fmj.models import ApiModel
from arcam.fmj.packets import AmxDuetResponse
from arcam.fmj.state import State

HDA = ApiModel.APIHDA_SERIES

#: What an SDR-35 on a non-tuner, non-network input re-reads every five seconds.
STREAM = {
    CommandCodes.DECODE_MODE_STATUS_2CH,
    CommandCodes.DECODE_MODE_STATUS_MCH,
    CommandCodes.GENERAL_SETUP,
    CommandCodes.INCOMING_VIDEO_PARAMETERS,
    CommandCodes.INCOMING_AUDIO_FORMAT,
    CommandCodes.INCOMING_AUDIO_SAMPLE_RATE,
}
CORE = {
    CommandCodes.POWER,
    CommandCodes.VOLUME,
    CommandCodes.MUTE,
    CommandCodes.CURRENT_SOURCE,
}


class FakeUnit:
    """Answers update requests from a table and records what was asked."""

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.values: dict[CommandCodes, bytes] = {}
        self.timeouts: set[CommandCodes] = set()
        self.requested: list[CommandCodes] = []
        #: Seconds each request takes, on the fake clock.
        self.latency = 0.0
        self.power(True)
        self.source(SourceCodes.BD)

    def power(self, on: bool) -> None:
        self.values[CommandCodes.POWER] = bytes([0x01 if on else 0x00])

    def source(self, source: SourceCodes) -> None:
        self.values[CommandCodes.CURRENT_SOURCE] = source.to_bytes(HDA, 1)

    async def request(self, zn, cc, data, priority=0):
        self.requested.append(cc)
        self.clock[0] += self.latency
        if cc in self.timeouts:
            raise TimeoutError
        if cc == CommandCodes.PRESET_DETAIL:
            # End the preset scan at the first slot.
            raise CommandInvalidAtThisTime()
        return self.values.get(cc, bytes([0x00]))


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr("arcam.fmj.state._monotonic", lambda: now[0])
    return now


@pytest.fixture
def unit(clock) -> FakeUnit:
    return FakeUnit(clock)


@pytest.fixture
def state(unit) -> State:
    client = MagicMock(spec=Client)
    client.connected = True
    client.connection_id = 1
    client.request.side_effect = unit.request
    state = State(client, 1)
    state._amxduet = AmxDuetResponse({"Device-Make": "JBL", "Device-Model": "SDR-35"})
    return state


async def run_pass(state: State, unit: FakeUnit) -> set[CommandCodes]:
    unit.requested.clear()
    await asyncio.gather(*await state.get_update_tasks())
    return set(unit.requested)


def push(state: State, cc: CommandCodes, data: bytes) -> None:
    """Record an unsolicited status message, as State._listen does."""
    state._state[cc] = data


async def test_first_pass_reads_everything(state, unit):
    requested = await run_pass(state, unit)
    assert CORE | STREAM <= requested
    assert {
        CommandCodes.BASS_EQUALIZATION,
        CommandCodes.ROOM_EQUALIZATION,
        CommandCodes.ROOM_EQ_NAMES,
    } <= requested
    assert state._updated.is_set()


async def test_idle_until_a_tier_is_due(state, unit, clock):
    await run_pass(state, unit)
    unit.requested.clear()
    clock[0] += 4.9
    assert await state.get_update_tasks() == []
    assert unit.requested == []


async def test_stream_and_unpushed_state_every_five_seconds(state, unit, clock):
    await run_pass(state, unit)
    clock[0] += 5
    assert await run_pass(state, unit) == STREAM


async def test_network_playback_joins_the_fast_tier_on_net(state, unit, clock):
    unit.source(SourceCodes.NET)
    await run_pass(state, unit)
    clock[0] += 5
    requested = await run_pass(state, unit)
    assert {
        CommandCodes.NETWORK_PLAYBACK_STATUS,
        CommandCodes.NOW_PLAYING_INFO,
    } <= requested
    assert CommandCodes.POWER not in requested


async def test_core_state_safety_net_every_thirty_seconds(state, unit, clock):
    await run_pass(state, unit)
    clock[0] += 30
    requested = await run_pass(state, unit)
    assert requested == CORE | STREAM


async def test_everything_else_safety_net_every_minute(state, unit, clock):
    await run_pass(state, unit)
    clock[0] += 60
    requested = await run_pass(state, unit)
    assert CORE | STREAM <= requested
    assert CommandCodes.BASS_EQUALIZATION in requested
    # read once per connection
    assert CommandCodes.ROOM_EQ_NAMES not in requested
    assert CommandCodes.SOFTWARE_VERSION not in requested


async def test_interval_counts_from_the_end_of_a_pass(state, unit, clock):
    """A slow unit still gets a rest between passes."""
    unit.latency = 1.0
    await run_pass(state, unit)
    unit.latency = 0.0
    clock[0] += 4.9
    assert await run_pass(state, unit) == set()
    clock[0] += 0.1
    assert await run_pass(state, unit) == STREAM


async def test_standby_reads_only_power(state, unit, clock):
    unit.power(False)
    await run_pass(state, unit)
    clock[0] += 60
    assert await run_pass(state, unit) == {CommandCodes.POWER}


async def test_power_on_rereads_everything_then_again_once_settled(
    state, unit, clock
):
    unit.power(False)
    await run_pass(state, unit)
    clock[0] += 1

    unit.power(True)
    push(state, CommandCodes.POWER, bytes([0x01]))
    requested = await run_pass(state, unit)
    assert CommandCodes.BASS_EQUALIZATION in requested

    # The settling re-read replaces the usual five-second fast tier...
    clock[0] += 9.9
    assert await run_pass(state, unit) == set()
    clock[0] += 0.1
    assert CommandCodes.BASS_EQUALIZATION in await run_pass(state, unit)
    # ...and then the usual cadence resumes.
    clock[0] += 5
    assert await run_pass(state, unit) == STREAM


async def test_source_change_rereads_everything(state, unit, clock):
    await run_pass(state, unit)
    clock[0] += 1

    unit.source(SourceCodes.FM)
    push(state, CommandCodes.CURRENT_SOURCE, SourceCodes.FM.to_bytes(HDA, 1))
    requested = await run_pass(state, unit)
    # per-input settings follow the source
    assert CommandCodes.BASS_EQUALIZATION in requested
    # and tuner information now applies
    assert {CommandCodes.RDS_INFORMATION, CommandCodes.PRESET_DETAIL} <= requested


async def test_presets_are_not_on_the_safety_net_cycle(state, unit, clock):
    unit.source(SourceCodes.FM)
    assert CommandCodes.PRESET_DETAIL in await run_pass(state, unit)
    clock[0] += 60
    requested = await run_pass(state, unit)
    assert CommandCodes.BASS_EQUALIZATION in requested
    assert CommandCodes.PRESET_DETAIL not in requested


async def test_room_eq_names_retried_after_a_timeout(state, unit, clock):
    """A timeout leaves no answer recorded, so the safety net asks again."""
    unit.timeouts.add(CommandCodes.ROOM_EQ_NAMES)
    await run_pass(state, unit)
    assert CommandCodes.ROOM_EQ_NAMES not in state._state

    unit.timeouts.clear()
    clock[0] += 60
    assert CommandCodes.ROOM_EQ_NAMES in await run_pass(state, unit)
    clock[0] += 60
    assert CommandCodes.ROOM_EQ_NAMES not in await run_pass(state, unit)


async def test_new_connection_rereads_everything(state, unit, clock):
    """The update loop never runs while disconnected, so a reconnect has to be
    noticed from the client's connection id."""
    await run_pass(state, unit)
    clock[0] += 1
    push(state, CommandCodes.BASS_EQUALIZATION, bytes([0x03]))

    state._client.connection_id = 2
    tasks = await state.get_update_tasks()
    # the cache from the old connection is gone before the pass even runs
    assert CommandCodes.BASS_EQUALIZATION not in state._state
    assert not state._updated.is_set()

    unit.requested.clear()
    await asyncio.gather(*tasks)
    requested = set(unit.requested)
    assert CORE | STREAM <= requested
    assert {CommandCodes.BASS_EQUALIZATION, CommandCodes.ROOM_EQ_NAMES} <= requested
    assert state._updated.is_set()


async def test_state_pushed_before_the_first_pass_is_kept(state, unit):
    push(state, CommandCodes.CURRENT_SOURCE, SourceCodes.CD.to_bytes(HDA, 1))
    unit.source(SourceCodes.CD)
    requested = await run_pass(state, unit)
    # source gating used the pushed source
    assert CommandCodes.NETWORK_PLAYBACK_STATUS not in requested
