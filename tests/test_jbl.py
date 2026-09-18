"""Tests for JBL Synthesis (SDR-35/SDR-38/SDP-55/SDP-58) support."""
import asyncio
from datetime import timedelta

import pytest
from unittest.mock import MagicMock

from arcam.fmj.client import Client
from arcam.fmj.codecs import (
    AnswerCodes,
    CompressionMode,
    ControlOption,
    DecodeMode2CH,
    DecodeModeMCH,
    DeviceRegion,
    DisplayOnTime,
    EngineeringMenuInfo,
    GeneralSetup,
    IncomingAudioConfig,
    IncomingAudioFormat,
    IncomingVideoAspectRatio,
    IncomingVideoColorspace,
    MenuLanguage,
    PowerOnOption,
    SourceCodes,
)
from arcam.fmj.commands import CommandCodes
from arcam.fmj.models import ApiModel, api_model_for
from arcam.fmj.packets import AmxDuetResponse, ResponsePacket
from arcam.fmj.rc5 import RC5CodeNavigation
from arcam.fmj.state import State

# 2ch and multi-channel incoming formats, to steer State.get_2ch()
FORMAT_PCM_STEREO = bytes([0x00, 0x02])
FORMAT_ATMOS = bytes([0x16, 0x1C])

GENERAL_SETUP_DATA = bytes(
    [
        *b"PIANO     ",  # Data1-10: input name
        0x16,  # Data11: Dolby Atmos
        0x1C,  # Data12: 7.1
        0x02,  # Data13: 48kHz
        0x1F,  # Data14: lossless
        0x04,  # Data15: dialnorm 4dB
        0x0F, 0x00,  # Data16-17: 3840 horizontal
        0x08, 0x70,  # Data18-19: 2160 vertical
        0x3C,  # Data20: 60Hz
        0x00,  # Data21: progressive
        0x02,  # Data22: 16:9
        0x01,  # Data23: HDR10
        0x00,  # Data24: compression off
        0x82,  # Data25: balance -2
        0x00,  # Data26: DTS dialogue control 0
        0x53,  # Data27: maximum volume 83
        0x2D,  # Data28: maximum on volume 45
        0x04,  # Data29: display always on
        0x02,  # Data30: control via IP
        0x01,  # Data31: power on to standby
        0x00,  # Data32: English
    ]
)

ENGINEERING_MENU_DATA = bytes(
    [
        *([0x00] * 10),  # Data1-10: set-only fields and PIN
        0x03,  # Data11: region Australia
        0x00,  # Data12: remote code 16
        0x00,  # Data13: standby mode auto
        0x00,  # Data14: protection sensitivity high
        0x00,  # Data15: use display HDMI
        0x00,  # Data16: display type 16:9
        0x01,  # Data17: DANTE enabled
        0x01,  # Data18: C4 SDDP enabled
        0x00,  # Data19: send C4 identify
        0x00,  # Data20: shutdown code normal
        *b"2.01/0.03",  # Data21-29: host version
        *b"1.03",  # Data30-33: DSP version
        *b"0.11",  # Data34-37: OSD version
        *b"2.5.13.50017-6",  # Data38-51: NET version
    ]
)


def make_state(model: str, zn: int = 1) -> State:
    client = MagicMock(spec=Client)
    state = State(client, zn)
    state._amxduet = AmxDuetResponse(
        {"Device-Make": "JBL", "Device-Model": model}
    )
    return state


@pytest.mark.parametrize("model", ["SDR-35", "SDR-38", "SDP-55", "SDP-58"])
async def test_jbl_models_map_to_hda(model):
    assert api_model_for(model) == ApiModel.APIHDA_SERIES


async def test_unknown_model_still_falls_back():
    assert api_model_for("SDR-99") == ApiModel.API450_SERIES


async def test_jbl_uses_hda_source_map():
    state = make_state("SDR-35")
    assert SourceCodes.UHD in state.get_source_list()
    assert SourceCodes.BT in state.get_source_list()


async def test_logic16_reported_2ch():
    state = make_state("SDR-35")
    state._state[CommandCodes.DECODE_MODE_STATUS_2CH] = bytes([0x0B])
    assert state.get_decode_mode_2ch() == DecodeMode2CH.LOGIC_16_IMMERSION


async def test_logic16_reported_mch():
    state = make_state("SDR-35")
    state._state[CommandCodes.DECODE_MODE_STATUS_MCH] = bytes([0x0B])
    assert state.get_decode_mode_mch() == DecodeModeMCH.LOGIC_16_IMMERSION


async def test_logic16_listed_only_on_jbl():
    jbl = make_state("SDR-35")
    arcam = make_state("AVR30")
    for state in (jbl, arcam):
        state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = FORMAT_ATMOS
    assert DecodeModeMCH.LOGIC_16_IMMERSION in jbl.get_decode_modes()
    assert DecodeModeMCH.LOGIC_16_IMMERSION not in arcam.get_decode_modes()
    for state in (jbl, arcam):
        state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = FORMAT_PCM_STEREO
    assert DecodeMode2CH.LOGIC_16_IMMERSION in jbl.get_decode_modes()
    assert DecodeMode2CH.LOGIC_16_IMMERSION not in arcam.get_decode_modes()


async def test_auro_filtered_on_non_premium_hda():
    """AVR5 is HDA but not premium; Auro modes must not be offered."""
    state = make_state("AVR5")
    state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = FORMAT_PCM_STEREO
    modes = state.get_decode_modes()
    assert DecodeMode2CH.AURO_NATIVE not in modes
    assert DecodeMode2CH.STEREO in modes


async def test_set_logic16():
    state = make_state("SDR-35")
    state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = FORMAT_ATMOS
    await state.set_decode_mode_mch(DecodeModeMCH.LOGIC_16_IMMERSION)
    state.client.request.assert_called_with(
        1, CommandCodes.SIMULATE_RC5_IR_COMMAND, bytes([16, 114]), 0
    )


async def test_general_setup_decode():
    setup = GeneralSetup.from_bytes(GENERAL_SETUP_DATA)
    assert setup.source_name == "PIANO"
    assert setup.audio_format == IncomingAudioFormat.DOLBY_ATMOS
    assert setup.audio_config == IncomingAudioConfig.STEREO_CENTER_SURR_LR_BACK_LR_LFE
    assert setup.sample_rate == 48000
    assert setup.bitrate == "lossless"
    assert setup.dialnorm == 4
    assert setup.horizontal_resolution == 3840
    assert setup.vertical_resolution == 2160
    assert setup.refresh_rate == 60
    assert setup.interlaced is False
    assert setup.aspect_ratio == IncomingVideoAspectRatio.ASPECT_16_9
    assert setup.colorspace == IncomingVideoColorspace.HDR10
    assert setup.compression == CompressionMode.OFF
    assert setup.balance == -2
    assert setup.dts_dialogue_control == 0
    assert setup.max_volume == 83
    assert setup.max_on_volume == 45
    assert setup.display_on_time == DisplayOnTime.ALWAYS_ON
    assert setup.control_option == ControlOption.IP
    assert setup.power_on_option == PowerOnOption.STANDBY
    assert setup.language == MenuLanguage.ENGLISH


async def test_general_setup_no_stream():
    """Dialnorm out of the documented range decodes to None."""
    data = bytearray(GENERAL_SETUP_DATA)
    data[14] = 0xFF
    setup = GeneralSetup.from_bytes(bytes(data))
    assert setup.dialnorm is None


async def test_general_setup_too_short():
    with pytest.raises(ValueError):
        GeneralSetup.from_bytes(GENERAL_SETUP_DATA[:31])


async def test_general_setup_state():
    state = make_state("SDR-35")
    assert state.get_general_setup() is None
    state._state[CommandCodes.GENERAL_SETUP] = GENERAL_SETUP_DATA
    setup = state.get_general_setup()
    assert setup is not None
    assert setup.source_name == "PIANO"
    assert state.to_dict()["GENERAL_SETUP"] == setup


async def test_engineering_menu_decode():
    info = EngineeringMenuInfo.from_bytes(ENGINEERING_MENU_DATA)
    assert info.region == DeviceRegion.AUSTRALIA
    assert info.remote_code == 0
    assert info.dante_enabled is True
    assert info.c4_sddp_enabled is True
    assert info.host_version == "2.01/0.03"
    assert info.dsp_version == "1.03"
    assert info.osd_version == "0.11"
    assert info.net_version == "2.5.13.50017-6"


async def test_engineering_menu_short_response():
    """Truncated version fields decode defensively rather than raising."""
    info = EngineeringMenuInfo.from_bytes(ENGINEERING_MENU_DATA[:43])
    assert info.region == DeviceRegion.AUSTRALIA
    assert info.net_version == "2.5.13"


async def test_engineering_menu_too_short():
    with pytest.raises(ValueError):
        EngineeringMenuInfo.from_bytes(ENGINEERING_MENU_DATA[:19])


async def test_multichannel_pcm_is_not_2ch():
    """7.1 PCM must use the multi-channel decode modes.

    Observed on an SDR-35: with multi-channel PCM playing, the 2ch decode
    query answers 0x85 (invalid at this time) while the MCH query answers.
    """
    state = make_state("SDR-35")
    state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = bytes([0x00, 0x1C])
    assert not state.get_2ch()
    state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = bytes([0x00, 0x02])
    assert state.get_2ch()
    # An undetected configuration keeps the historical format-based answer.
    state._state[CommandCodes.INCOMING_AUDIO_FORMAT] = bytes([0x00, 0x21])
    assert state.get_2ch()


@pytest.mark.parametrize(
    ("mute", "status", "confirmed"),
    [
        (True, bytes([0x00]), True),
        (False, bytes([0x01]), True),
        (True, bytes([0x01]), False),
    ],
)
async def test_mute_rc5_without_echo(mute, status, confirmed, monkeypatch):
    """Mute must not fail when only the status push answers.

    Observed on an SDR-35: the discrete mute RC5 codes are executed but the
    0x08 frame is never echoed, so the request times out while the 0x0E
    status arrives as a push.
    """
    # No push arrives here; go straight to the read-back.
    monkeypatch.setattr("arcam.fmj.state._CONFIRM_TIMEOUT", timedelta(0))
    state = make_state("SDR-35")

    async def request(zn, cc, data, priority=0):
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            raise TimeoutError
        assert cc == CommandCodes.MUTE
        return status

    state.client.request.side_effect = request
    if confirmed:
        await state.set_mute(mute)
        assert state.get_mute() is mute
    else:
        with pytest.raises(TimeoutError):
            await state.set_mute(mute)


async def test_rc5_confirmed_by_the_status_push_without_a_read():
    """The push the command causes is the confirmation; no read is needed."""
    state = make_state("SDR-35")
    requests = []

    async def request(zn, cc, data, priority=0):
        requests.append(cc)
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            state._listen(
                ResponsePacket(zn, CommandCodes.MUTE, AnswerCodes.STATUS_UPDATE, bytes([0x00]))
            )
            raise TimeoutError  # carried out, never echoed
        raise AssertionError(f"unexpected request {cc}")

    state.client.request.side_effect = request
    await state.set_mute(True)
    assert state.get_mute() is True
    assert requests == [CommandCodes.SIMULATE_RC5_IR_COMMAND]


async def test_unechoed_rc5_without_expectation_is_not_an_error():
    """A command with no state to confirm returns once it has been sent."""
    state = make_state("SDR-35")

    async def request(zn, cc, data, priority=0):
        raise TimeoutError  # never echoed

    state.client.request.side_effect = request
    await state.send_navigation(RC5CodeNavigation.UP)
    await state.send_numeric(1)
    assert state.client.request.call_count == 2


# --- Volume steps ----------------------------------------------------------


class FakeVolume:
    """Answers VOLUME reads and writes like the unit; any RC5 is recorded."""

    def __init__(self, state: State, volume: int | None) -> None:
        self.state = state
        self.volume = volume
        self.writes: list[int] = []
        self.rc5: list[bytes] = []
        state.client.request.side_effect = self.request

    async def request(self, zn, cc, data, priority=0):
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            self.rc5.append(bytes(data))
            raise TimeoutError  # never echoed
        assert cc == CommandCodes.VOLUME
        if data == bytes([0xF0]):
            if self.volume is None:
                raise TimeoutError
            return bytes([self.volume])
        self.volume = data[0]
        self.writes.append(data[0])
        return bytes([self.volume])


@pytest.mark.parametrize(
    ("volume", "up", "writes", "after"),
    [
        (61, True, [62], 62),
        (61, False, [60], 60),
        (99, True, [], 99),
        (0, False, [], 0),
    ],
)
async def test_volume_step_writes_an_absolute_value(volume, up, writes, after):
    """One step is one unit, written directly: exact, echoed, never RC5.

    On an SDR-35 one RC5 volume step, re-sent after a timeout because the
    unit never echoes RC5, moved the volume two units.
    """
    state = make_state("SDR-35")
    unit = FakeVolume(state, volume)

    await (state.inc_volume() if up else state.dec_volume())

    assert unit.writes == writes
    assert unit.rc5 == []
    assert state.get_volume() == after


async def test_volume_step_reads_the_current_volume_first():
    """Another controller may have moved it since the last push reached us."""
    state = make_state("SDR-35")
    state._state[CommandCodes.VOLUME] = bytes([40])  # stale
    unit = FakeVolume(state, 61)

    await state.inc_volume()

    assert unit.writes == [62]


async def test_volume_steps_run_one_after_another():
    state = make_state("SDR-35")
    unit = FakeVolume(state, 61)

    await asyncio.gather(state.inc_volume(), state.inc_volume())

    assert unit.writes == [62, 63]


async def test_volume_step_uses_the_cached_value_when_the_read_fails():
    state = make_state("SDR-35")
    state._state[CommandCodes.VOLUME] = bytes([61])
    unit = FakeVolume(state, None)  # reads time out

    await state.inc_volume()

    assert unit.writes == [62]
    assert unit.rc5 == []


async def test_volume_step_falls_back_to_one_rc5_step_when_unknown():
    state = make_state("SDR-35")
    unit = FakeVolume(state, None)

    await state.dec_volume()

    assert unit.writes == []
    assert unit.rc5 == [bytes([16, 17])]


async def test_volume_step_on_st_series_uses_the_step_command():
    """The ST60 steps with 0xF1/0xF2 on 0x0D, which it echoes."""
    client = MagicMock(spec=Client)
    state = State(client, 1)
    state._amxduet = AmxDuetResponse({"Device-Model": "ST60"})

    await state.inc_volume()
    await state.dec_volume()

    assert [call.args[2] for call in client.request.call_args_list] == [
        bytes([0xF1]),
        bytes([0xF2]),
    ]


@pytest.mark.parametrize(
    ("action", "cc", "status", "confirmed"),
    [
        (
            lambda s: s.set_source(SourceCodes.CD),
            CommandCodes.CURRENT_SOURCE,
            bytes([0x01]),
            True,
        ),
        (
            lambda s: s.set_source(SourceCodes.CD),
            CommandCodes.CURRENT_SOURCE,
            bytes([0x02]),
            False,
        ),
        (
            lambda s: s.set_decode_mode_mch(DecodeModeMCH.LOGIC_16_IMMERSION),
            CommandCodes.DECODE_MODE_STATUS_MCH,
            bytes([0x0B]),
            True,
        ),
        (
            lambda s: s.set_decode_mode_2ch(DecodeMode2CH.STEREO),
            CommandCodes.DECODE_MODE_STATUS_2CH,
            bytes([0x01]),
            True,
        ),
        (lambda s: s.set_power(True), CommandCodes.POWER, bytes([0x01]), True),
        (lambda s: s.set_power(False), CommandCodes.POWER, bytes([0x00]), True),
    ],
)
async def test_rc5_commands_without_echo(action, cc, status, confirmed, monkeypatch):
    """RC5-simulated commands must not fail when only the status push answers.

    Same firmware behaviour as mute, observed on an SDR-35 for input
    selection as well: the command executes but the 0x08 frame is never
    echoed.
    """
    # No push arrives here; go straight to the read-back.
    monkeypatch.setattr("arcam.fmj.state._CONFIRM_TIMEOUT", timedelta(0))
    state = make_state("SDR-35")

    async def request(zn, command, data, priority=0):
        if command == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            raise TimeoutError
        assert command == cc
        return status

    state.client.request.side_effect = request
    if confirmed:
        await action(state)
        assert state._state[cc] == status
    else:
        with pytest.raises(TimeoutError):
            await action(state)
