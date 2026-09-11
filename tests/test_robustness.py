"""Behaviour borrowed from the vendor (Control4/Crestron/RTI) drivers:
paced, source-aware polling; power-on before source select with
verification; CC-level tone/trim stepping.
"""

import asyncio
import pytest
from datetime import timedelta
from unittest.mock import MagicMock

from arcam.fmj import state as state_module
from arcam.fmj.client import Client
from arcam.fmj.codecs import AnswerCodes, NowPlayingInfo, SourceCodes
from arcam.fmj.commands import CommandCodes
from arcam.fmj.errors import CommandInvalidAtThisTime, UnsupportedCommand
from arcam.fmj.models import ApiModel
from arcam.fmj.packets import AmxDuetResponse, ResponsePacket
from arcam.fmj.state import State

MODEL_FOR_API = {
    ApiModel.API450_SERIES: "AVR450",
    ApiModel.API860_SERIES: "AVR850",
    ApiModel.APIHDA_SERIES: "SDR-35",
    ApiModel.APISA_SERIES: "SA30",
}

_HDA_SOURCE = {
    src: src.to_bytes(ApiModel.APIHDA_SERIES, 1)
    for src in (
        SourceCodes.BD, SourceCodes.FM, SourceCodes.DAB, SourceCodes.NET,
        SourceCodes.BT, SourceCodes.FOLLOW_ZONE_1,
    )
}


def make_state(api_model=ApiModel.APIHDA_SERIES, zn=1, connected=True):
    client = MagicMock(spec=Client)
    client.connected = connected
    client.request.side_effect = CommandInvalidAtThisTime()
    state = State(client, zn)
    state._amxduet = AmxDuetResponse({"Device-Model": MODEL_FOR_API[api_model]})
    return client, state


async def requested(state) -> list[CommandCodes]:
    """Run one update pass and return the command codes it asked for."""
    state.client.request.reset_mock()
    await asyncio.gather(*await state.get_update_tasks())
    return [call.args[1] for call in state.client.request.call_args_list]


def _set(state, power: bool | None, source: SourceCodes | None):
    if power is not None:
        state._state[CommandCodes.POWER] = bytes([0x01 if power else 0x00])
    if source is not None:
        state._state[CommandCodes.CURRENT_SOURCE] = _HDA_SOURCE[source]


# --- Polling tiers ---


async def test_first_pass_reads_everything_but_presets():
    _, state = make_state()
    codes = await requested(state)
    assert CommandCodes.POWER in codes
    assert CommandCodes.VOLUME in codes
    assert CommandCodes.TREBLE_EQUALIZATION in codes
    assert CommandCodes.NOW_PLAYING_INFO in codes  # source unknown: permissive
    assert CommandCodes.BLUETOOTH_STATUS in codes
    # Presets wait until a tuner source is known to be selected.
    assert CommandCodes.PRESET_DETAIL not in codes


async def test_second_immediate_pass_reads_nothing():
    _, state = make_state()
    await requested(state)
    _set(state, power=True, source=SourceCodes.BD)
    state._poll_source = SourceCodes.BD
    state._poll_power = True
    assert await requested(state) == []


async def test_tiers_come_due_independently():
    _, state = make_state()
    await requested(state)
    _set(state, power=True, source=SourceCodes.BD)
    state._poll_source, state._poll_power = SourceCodes.BD, True

    state._poll_due["fast"] = 0.0
    codes = await requested(state)
    assert set(codes) == {
        CommandCodes.POWER, CommandCodes.VOLUME, CommandCodes.MUTE, CommandCodes.CURRENT_SOURCE,
    }

    state._poll_due["slow"] = 0.0
    codes = await requested(state)
    assert CommandCodes.TREBLE_EQUALIZATION in codes
    assert CommandCodes.DECODE_MODE_STATUS_2CH in codes
    assert CommandCodes.POWER not in codes


async def test_standby_polls_only_power():
    _, state = make_state()
    await requested(state)
    _set(state, power=False, source=SourceCodes.BD)
    state._poll_due = {"fast": 0.0, "source": 0.0, "slow": 0.0}
    assert await requested(state) == [CommandCodes.POWER]


async def test_power_on_brings_every_tier_forward():
    _, state = make_state()
    await requested(state)
    _set(state, power=False, source=SourceCodes.BD)
    state._poll_due = {"fast": 0.0, "source": 0.0, "slow": 0.0}
    await requested(state)  # standby pass; tiers now scheduled far ahead
    assert all(due > 0 for due in state._poll_due.values())

    _set(state, power=True, source=SourceCodes.BD)
    codes = await requested(state)
    assert CommandCodes.VOLUME in codes
    assert CommandCodes.TREBLE_EQUALIZATION in codes


@pytest.mark.parametrize("source, expected, unexpected", [
    (SourceCodes.BD,
     set(),
     {CommandCodes.RDS_INFORMATION, CommandCodes.DAB_STATION, CommandCodes.DLS_PDT_INFO,
      CommandCodes.TUNER_PRESET, CommandCodes.NETWORK_PLAYBACK_STATUS,
      CommandCodes.NOW_PLAYING_INFO, CommandCodes.BLUETOOTH_STATUS}),
    (SourceCodes.FM,
     {CommandCodes.RDS_INFORMATION, CommandCodes.TUNER_PRESET},
     {CommandCodes.DAB_STATION, CommandCodes.NOW_PLAYING_INFO, CommandCodes.BLUETOOTH_STATUS}),
    (SourceCodes.DAB,
     {CommandCodes.DAB_STATION, CommandCodes.DLS_PDT_INFO, CommandCodes.TUNER_PRESET},
     {CommandCodes.RDS_INFORMATION, CommandCodes.NOW_PLAYING_INFO}),
    (SourceCodes.NET,
     {CommandCodes.NETWORK_PLAYBACK_STATUS, CommandCodes.NOW_PLAYING_INFO},
     {CommandCodes.RDS_INFORMATION, CommandCodes.BLUETOOTH_STATUS}),
    (SourceCodes.BT,
     # 0x64 carries track metadata for Bluetooth as well as network sources.
     {CommandCodes.BLUETOOTH_STATUS, CommandCodes.NOW_PLAYING_INFO},
     {CommandCodes.RDS_INFORMATION, CommandCodes.NETWORK_PLAYBACK_STATUS}),
])
async def test_source_specific_commands_follow_the_source(source, expected, unexpected):
    _, state = make_state()
    await requested(state)
    _set(state, power=True, source=source)
    state._poll_power = True
    codes = set(await requested(state))  # source changed: source tier is due
    assert expected <= codes
    assert not (unexpected & codes)


async def test_presets_read_once_per_tuner_selection():
    client, state = make_state()

    async def request(zn, cc, data, priority=0):
        if cc == CommandCodes.PRESET_DETAIL:
            if data == b"\x01":
                return b"\x01\x03SR P1"
            raise CommandInvalidAtThisTime()
        raise CommandInvalidAtThisTime()

    client.request.side_effect = request
    await requested(state)
    _set(state, power=True, source=SourceCodes.FM)
    state._poll_power = True

    codes = await requested(state)
    assert CommandCodes.PRESET_DETAIL in codes
    assert state.get_preset_details()[1].name == "SR P1"

    # Same tuner source, next source tick: no re-read of 50 presets.
    state._poll_due["source"] = 0.0
    assert CommandCodes.PRESET_DETAIL not in await requested(state)

    # Until explicitly asked for.
    state.refresh_presets()
    state._poll_due["source"] = 0.0
    assert CommandCodes.PRESET_DETAIL in await requested(state)

    # Or the other tuner source is selected.
    _set(state, power=True, source=SourceCodes.DAB)
    assert CommandCodes.PRESET_DETAIL in await requested(state)


async def test_leaving_a_source_drops_its_cached_info():
    _, state = make_state()
    await requested(state)
    _set(state, power=True, source=SourceCodes.NET)
    state._poll_power = True
    await requested(state)
    state._now_playing = NowPlayingInfo(track="x")
    state._state[CommandCodes.NETWORK_PLAYBACK_STATUS] = b"\x02"
    state._state[CommandCodes.RDS_INFORMATION] = b"stale"

    _set(state, power=True, source=SourceCodes.BD)
    await requested(state)
    assert state.get_now_playing() is None
    assert state.get_network_playback_status() is None
    assert state.get_rds_information() is None


async def test_disconnect_resets_polling_state():
    client, state = make_state()
    await requested(state)
    _set(state, power=True, source=SourceCodes.FM)
    state._presets_stale = False
    client.connected = False
    assert await state.get_update_tasks() == []
    assert state._poll_due == {"fast": 0.0, "source": 0.0, "slow": 0.0}
    assert state._presets_stale is True
    assert state.get_source() is None


# --- Source selection ---


@pytest.fixture
def fast_waits(mocker):
    mocker.patch.object(state_module, "_SOURCE_WAIT_INTERVAL", timedelta(milliseconds=5))
    mocker.patch.object(state_module, "_SOURCE_SETTLE_TIMEOUT", timedelta(milliseconds=60))
    mocker.patch.object(state_module, "_SOURCE_POWER_ON_TIMEOUT", timedelta(milliseconds=120))


def _rc5(src: SourceCodes, zn=1) -> bytes:
    from arcam.fmj.rc5 import RC5CODE_SOURCE
    return RC5CODE_SOURCE[(ApiModel.APIHDA_SERIES, zn)][src]


async def test_set_source_powers_on_first_and_waits_for_echo(fast_waits):
    client, state = make_state()
    _set(state, power=False, source=SourceCodes.BD)
    sent: list[bytes] = []

    power = b"\x00"

    async def request(zn, cc, data, priority=0):
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            sent.append(data)
            if data == bytes([16, 123]):  # power on: echo arrives a little later
                async def wake():
                    nonlocal power
                    await asyncio.sleep(0.02)
                    power = b"\x01"
                    state._listen(ResponsePacket(1, CommandCodes.POWER, AnswerCodes.STATUS_UPDATE, power))
                asyncio.get_running_loop().create_task(wake())
            elif data == _rc5(SourceCodes.NET):
                state._listen(ResponsePacket(
                    1, CommandCodes.CURRENT_SOURCE, AnswerCodes.STATUS_UPDATE, _HDA_SOURCE[SourceCodes.NET]))
            return data
        if cc == CommandCodes.POWER:
            return power  # periodic re-read while waiting
        raise CommandInvalidAtThisTime()

    client.request.side_effect = request
    await state.set_source(SourceCodes.NET)

    assert sent == [bytes([16, 123]), _rc5(SourceCodes.NET)]
    assert state.get_power() is True
    assert state.get_source() == SourceCodes.NET


async def test_set_source_resends_once_when_not_confirmed(fast_waits):
    client, state = make_state()
    _set(state, power=True, source=SourceCodes.BD)
    calls: list[tuple] = []

    async def request(zn, cc, data, priority=0):
        calls.append((cc, data))
        if cc == CommandCodes.CURRENT_SOURCE:
            return _HDA_SOURCE[SourceCodes.BD]  # unit never switched
        return data

    client.request.side_effect = request
    await state.set_source(SourceCodes.NET)

    rc5_sends = [c for c in calls if c[0] == CommandCodes.SIMULATE_RC5_IR_COMMAND]
    assert rc5_sends == [(CommandCodes.SIMULATE_RC5_IR_COMMAND, _rc5(SourceCodes.NET))] * 2
    # At least one explicit read-back was made between the two sends.
    assert (CommandCodes.CURRENT_SOURCE, b"\xf0") in calls


async def test_set_source_no_resend_when_confirmed_by_readback(fast_waits):
    client, state = make_state()
    _set(state, power=True, source=SourceCodes.BD)
    rc5_count = 0

    async def request(zn, cc, data, priority=0):
        nonlocal rc5_count
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            rc5_count += 1
        if cc == CommandCodes.CURRENT_SOURCE:
            return _HDA_SOURCE[SourceCodes.NET]  # switched, but never pushed
        return data

    client.request.side_effect = request
    await state.set_source(SourceCodes.NET)
    assert rc5_count == 1
    assert state.get_source() == SourceCodes.NET


async def test_set_source_when_power_unknown_does_not_power_on(fast_waits):
    client, state = make_state()
    sent = []

    async def request(zn, cc, data, priority=0):
        sent.append((cc, data))
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            state._listen(ResponsePacket(
                1, CommandCodes.CURRENT_SOURCE, AnswerCodes.STATUS_UPDATE, _HDA_SOURCE[SourceCodes.NET]))
        return data

    client.request.side_effect = request
    await state.set_source(SourceCodes.NET)
    assert bytes([16, 123]) not in [d for _, d in sent]


# --- CC-level stepping ---


@pytest.mark.parametrize("method, cc, data", [
    ("inc_treble_equalization", CommandCodes.TREBLE_EQUALIZATION, 0xF1),
    ("dec_treble_equalization", CommandCodes.TREBLE_EQUALIZATION, 0xF2),
    ("inc_balance", CommandCodes.BALANCE, 0xF1),
    ("dec_balance", CommandCodes.BALANCE, 0xF2),
    ("inc_subwoofer_trim", CommandCodes.SUBWOOFER_TRIM, 0xF1),
    ("dec_subwoofer_trim", CommandCodes.SUBWOOFER_TRIM, 0xF2),
    ("inc_lipsync_delay", CommandCodes.LIPSYNC_DELAY, 0xF1),
    ("dec_lipsync_delay", CommandCodes.LIPSYNC_DELAY, 0xF2),
    ("inc_sub_stereo_trim", CommandCodes.SUB_STEREO_TRIM, 0xF1),
    ("dec_sub_stereo_trim", CommandCodes.SUB_STEREO_TRIM, 0xF2),
])
@pytest.mark.parametrize("api_model", [
    ApiModel.API450_SERIES, ApiModel.API860_SERIES, ApiModel.APIHDA_SERIES,
])
async def test_steps_use_cc_write_on_avr_models(api_model, method, cc, data):
    client, state = make_state(api_model)
    client.request.side_effect = None
    client.request.return_value = bytes([0x02])
    await getattr(state, method)()
    client.request.assert_called_once_with(1, cc, bytes([data]), 0)
    assert state.get(cc) == bytes([0x02])


async def test_balance_step_falls_back_to_rc5_on_sa():
    client, state = make_state(ApiModel.APISA_SERIES)
    client.request.side_effect = None
    await state.inc_balance()
    client.request.assert_called_once_with(
        1, CommandCodes.SIMULATE_RC5_IR_COMMAND, bytes([0x10, 0x28]), 0)


async def test_sub_stereo_step_unsupported_without_cc_or_rc5():
    client, state = make_state(ApiModel.APISA_SERIES)
    with pytest.raises(UnsupportedCommand):
        await state.inc_sub_stereo_trim()
    client.request.assert_not_called()


async def test_set_tone_caches_echoed_value():
    client, state = make_state()
    client.request.side_effect = None
    client.request.return_value = bytes([0x85])
    await state.set_balance(-5)
    client.request.assert_called_once_with(1, CommandCodes.BALANCE, bytes([0x85]), 0)
    assert state.get_balance() == -5.0


# --- Regressions found in review ---


async def test_presets_retry_after_transient_error():
    """A 0x85 on the first slot means 'tuner not ready', not 'no presets'."""
    client, state = make_state()
    ready = False

    async def request(zn, cc, data, priority=0):
        if cc == CommandCodes.PRESET_DETAIL:
            if ready and data == b"\x01":
                return b"\x01\x03SR P1"
            raise CommandInvalidAtThisTime()
        raise CommandInvalidAtThisTime()

    client.request.side_effect = request
    await requested(state)
    _set(state, power=True, source=SourceCodes.FM)
    state._poll_power = True

    assert CommandCodes.PRESET_DETAIL in await requested(state)
    assert state.get_preset_details() == {}
    assert state._presets_stale is True  # not cached as a known-empty list

    ready = True
    state._poll_due["source"] = 0.0
    assert CommandCodes.PRESET_DETAIL in await requested(state)
    assert state.get_preset_details()[1].name == "SR P1"


async def test_now_playing_keeps_leading_emoji_on_old_firmware():
    """0xF0-0xF4 are UTF-8 lead bytes; a real title must not lose one."""
    client, state = make_state()

    async def request(zn, cc, data, priority=0):
        if cc != CommandCodes.NOW_PLAYING_INFO:
            raise CommandInvalidAtThisTime()
        return {
            0xF0: "\N{MUSICAL NOTE} Song".encode(),      # old firmware, no echo
            0xF1: "\N{SNOWMAN} Band".encode(),
            0xF2: b"\xf2Album",                          # new firmware, echoed
            0xF3: b"",
            0xF4: b"\x01",
            0xF5: b"\xf5\x03",
        }[data[0]]

    client.request.side_effect = request
    _set(state, power=True, source=SourceCodes.NET)
    await asyncio.gather(*await state.get_update_tasks())

    info = state.get_now_playing()
    assert info.track == "\N{MUSICAL NOTE} Song"
    assert info.artist == "\N{SNOWMAN} Band"
    assert info.album == "Album"
    assert info.sample_rate == 44100
    assert info.encoder.name == "FLAC"


async def test_now_playing_echo_only_response_is_survivable():
    """Nothing playing: an echo with no payload must not raise."""
    client, state = make_state()

    async def request(zn, cc, data, priority=0):
        if cc != CommandCodes.NOW_PLAYING_INFO:
            raise CommandInvalidAtThisTime()
        return bytes([data[0]])  # echo only

    client.request.side_effect = request
    _set(state, power=True, source=SourceCodes.NET)
    await asyncio.gather(*await state.get_update_tasks())
    assert state.get_now_playing() is None


async def test_zone_2_following_zone_1_keeps_polling_source_items():
    """FOLLOW_ZONE_1 hides the real source, so poll permissively and drop nothing."""
    _, state = make_state(zn=2)
    await requested(state)
    state._state[CommandCodes.POWER] = b"\x01"
    state._state[CommandCodes.CURRENT_SOURCE] = _HDA_SOURCE[SourceCodes.FOLLOW_ZONE_1]
    state._poll_power = True
    # FOLLOW_ZONE_1 reads as "unknown source", which is what the first pass
    # already recorded, so nothing brings the source tier forward on its own.
    state._poll_due["source"] = 0.0

    codes = set(await requested(state))
    assert state.get_source() == SourceCodes.FOLLOW_ZONE_1
    assert CommandCodes.RDS_INFORMATION in codes
    assert CommandCodes.DAB_STATION in codes


@pytest.mark.parametrize("source", [None, SourceCodes.FOLLOW_ZONE_1])
async def test_indeterminate_source_drops_nothing(source):
    """Unknown, or following zone 1: the real source could be anything."""
    _, state = make_state()
    state._state[CommandCodes.RDS_INFORMATION] = b"text"
    state._now_playing = NowPlayingInfo(track="x")
    state._drop_source_state(source)
    assert state.get_rds_information() == "text"
    assert state.get_now_playing() is not None


async def test_connect_does_not_read_everything_twice():
    """The pass after the initial full read must not repeat it."""
    client, state = make_state()

    async def request(zn, cc, data, priority=0):
        if cc == CommandCodes.POWER:
            return b"\x01"
        if cc == CommandCodes.CURRENT_SOURCE:
            return _HDA_SOURCE[SourceCodes.BD]
        raise CommandInvalidAtThisTime()

    client.request.side_effect = request

    first = await requested(state)
    assert len(first) > 10
    assert await requested(state) == []
