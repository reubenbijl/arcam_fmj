"""Zone state"""

import asyncio
import enum
import logging
import time
from datetime import timedelta
from typing import Any, TypeVar

import attr

from .codecs import (
    AnswerCodes,
    BluetoothAudioStatus,
    CompressionMode,
    DecodeMode2CH,
    DecodeModeMCH,
    DisplayBrightness,
    DolbyAudioMode,
    EngineeringMenuInfo,
    GeneralSetup,
    HdmiOutput,
    IMAX_ENHANCED_SET_MAP,
    ImaxEnhancedMode,
    IncomingAudioConfig,
    IncomingAudioFormat,
    MenuCodes,
    NetworkPlaybackStatus,
    NowPlayingInfo,
    PresetDetail,
    RoomEqMode,
    SAMPLE_RATE_MAP,
    SAVE_RESTORE_CONFIRMATION,
    SaveRestoreSubCommand,
    SourceCodes,
    VideoParameters,
    VideoSelection,
    strip_now_playing_echo,
)
from .commands import (
    CommandCodes,
    CommandFlags,
    MUTE_WRITE_SUPPORTED,
    POWER_WRITE_SUPPORTED,
    SOURCE_WRITE_SUPPORTED,
    VOLUME_STEP_SUPPORTED,
)
from .errors import (
    CommandInvalidAtThisTime,
    CommandNotRecognised,
    NotConnectedException,
    ParameterNotRecognised,
    ResponseException,
    UnsupportedCommand,
    UnsupportedZone,
)
from .models import (
    APIVERSION_RC5_NUMERIC_SERIES,
    ApiModel,
    api_model_for,
)
from .packets import (
    AmxDuetRequest,
    AmxDuetResponse,
    ResponsePacket,
)
from .rc5 import (
    RC5CODE_BALANCE,
    RC5CODE_BASS,
    RC5CODE_COLOR,
    RC5CODE_DECODE_MODE_2CH,
    RC5CODE_DECODE_MODE_MCH,
    RC5CODE_DIRECT_MODE,
    RC5CODE_DISPLAY_BRIGHTNESS,
    RC5CODE_DOLBY_PLIIX_CENTRE_WIDTH,
    RC5CODE_DOLBY_PLIIX_DIMENSION,
    RC5CODE_DOLBY_PLIIX_PANORAMA,
    RC5CODE_HDMI_OUTPUT,
    RC5CODE_LIPSYNC,
    RC5CODE_MENU_ACCESS,
    RC5CODE_MUTE,
    RC5CODE_NAVIGATION,
    RC5CODE_PLAYBACK,
    RC5CODE_POWER,
    RC5CODE_SOURCE,
    RC5CODE_SUB_TRIM,
    RC5CODE_TOGGLE,
    RC5CODE_TREBLE,
    RC5CODE_VOLUME,
    RC5CodeColor,
    RC5CodeMenuAccess,
    RC5CodeNavigation,
    RC5CodePlayback,
    RC5CodeToggle,
)
from .client import Client, UpdateTask, _UPDATE_PRIORITY
from .utils import run_tasks, wait_any

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")

#: Channel configurations carrying at most two main channels; anything else
#: means a PCM or analogue stream is multi-channel despite its format byte.
_TWO_CHANNEL_CONFIGS = {
    IncomingAudioConfig.DUAL_MONO,
    IncomingAudioConfig.MONO,  # same value as CENTER_ONLY
    IncomingAudioConfig.STEREO_ONLY,
    IncomingAudioConfig.STEREO_DOWNMIX,
    IncomingAudioConfig.STEREO_ONLY_LO_RO,
    IncomingAudioConfig.DUAL_MONO_LFE,
    IncomingAudioConfig.MONO_LFE,  # same value as CENTER_LFE
    IncomingAudioConfig.STEREO_LFE,
    IncomingAudioConfig.STEREO_DOWNMIX_LFE,
    IncomingAudioConfig.STEREO_ONLY_LO_RO_LFE,
    IncomingAudioConfig.UNKNOWN,
    IncomingAudioConfig.UNDETECTED,
}

# --- Update pacing ---
#
# The unit pushes a status message for every change made with its front panel
# or remote (SH289E "State changes as a result of other inputs"), so most state
# needs no polling at all. Two kinds still do:
#
# - what the unit never pushes: network and Bluetooth playback (the NOT_PUSHED
#   flag), and the incoming stream. SH289E only promises pushes for changes a
#   user makes, not for a new signal arriving, and the decode mode that applies
#   switches with the stream too;
# - everything else, as a safety net: the unit sends its feedback to whichever
#   client made the most recent request (JBL's RTI driver documentation), so a
#   push can go to the JBL app or the unit's web page instead of here.
#
# The vendor drivers for these units all pace their polling this way, re-reading
# a small core set every few tens of seconds and the rest about once a minute.
# Re-reading everything back to back instead keeps the unit busy and makes this
# client the one that receives every other client's feedback.


class _PollTier(enum.Enum):
    """How often the update loop re-reads a command."""

    FAST = enum.auto()
    CORE = enum.auto()
    SLOW = enum.auto()


_POLL_INTERVALS = {
    _PollTier.FAST: timedelta(seconds=5),
    _PollTier.CORE: timedelta(seconds=30),
    _PollTier.SLOW: timedelta(seconds=60),
}

#: A zone that has just powered on answers some requests with errors while it
#: boots, so everything is read once more after this settling time.
_POLL_POWER_ON_SETTLE = timedelta(seconds=10)

_POLL_CORE_COMMANDS = frozenset({
    CommandCodes.POWER,
    CommandCodes.VOLUME,
    CommandCodes.MUTE,
    CommandCodes.CURRENT_SOURCE,
})

#: The incoming stream, and the decode mode read back for it.
_POLL_STREAM_COMMANDS = frozenset({
    CommandCodes.DECODE_MODE_STATUS_2CH,
    CommandCodes.DECODE_MODE_STATUS_MCH,
    CommandCodes.GENERAL_SETUP,
    CommandCodes.INCOMING_VIDEO_PARAMETERS,
    CommandCodes.INCOMING_AUDIO_FORMAT,
    CommandCodes.INCOMING_AUDIO_SAMPLE_RATE,
})

#: Fixed for the life of a connection: read until the unit gives an answer,
#: then not again until the next connection.
_POLL_ONCE_COMMANDS = frozenset({
    CommandCodes.ROOM_EQ_NAMES,
    CommandCodes.SOFTWARE_VERSION,
})


def _poll_tier(cc: CommandCodes) -> _PollTier:
    if cc.flags & CommandFlags.NOT_PUSHED or cc in _POLL_STREAM_COMMANDS:
        return _PollTier.FAST
    if cc in _POLL_CORE_COMMANDS:
        return _PollTier.CORE
    return _PollTier.SLOW


def _monotonic() -> float:
    """Seconds on the clock the update pacing runs on; replaced in tests."""
    return time.monotonic()

#: How long a command waits for the zone to report its effect, when the unit
#: does not echo the command itself (simulated RC5 on an SDR-35).
_CONFIRM_TIMEOUT = timedelta(seconds=2)

# --- Source selection ---
#
# A zone that is waking up can drop a source command, so every vendor driver
# for these units (Control4, Crestron, RTI, ELAN) powers the zone on first,
# then checks the source it reports and sends the command again if it did not
# take.

#: How long a zone in standby gets to report that it is on.
_SOURCE_POWER_ON_TIMEOUT = timedelta(seconds=15)
#: Source commands sent before giving up.
_SOURCE_ATTEMPTS = 3


def _get_byte(data: bytes | None) -> int | None:
    if data is None or len(data) != 1:
        return None
    return data[0]

def _get_scaled_negative(data: bytes | None, min_value: float, max_value: float, scale: float) -> float | None:
    byte_val = _get_byte(data)
    if byte_val is None:
        return None

    neg_limit = round(-min_value / scale) + 0x80
    pos_limit = round(max_value / scale)

    if byte_val >= 0x81 and byte_val <= neg_limit:
        return - (byte_val - 0x80) * scale
    if byte_val >= 0x00 and byte_val <= pos_limit:
        return  byte_val * scale
    return None

def _set_scaled(value: float, min_value: float, max_value: float, scale: float) -> bytes:
    value = max(min_value, min(max_value, value))
    value = round(value / scale)
    if value >= 0:
        return value
    else:
        return 0x80 - value

class State:
    _state: dict[int, bytes | None]
    _presets: dict[int, PresetDetail]

    def __init__(self, client: Client, zn: int) -> None:
        self._zn = zn
        self._client = client
        self._state = dict()
        self._presets = dict()
        self._now_playing: NowPlayingInfo | None = None
        self._amxduet: AmxDuetResponse | None = None
        self._unsupported_commands: set[CommandCodes] = set()
        self._updated = asyncio.Event()
        # Set whenever a status message for this zone is recorded.
        self._changed = asyncio.Event()
        # Update pacing, see _due_commands: the connection the cache belongs
        # to, when each tier is next due, and the power and source seen by the
        # previous pass, so that a change brings every tier forward.
        self._poll_connection: int | None = None
        self._poll_due: dict[_PollTier, float] = {}
        self._poll_power: bool | None = None
        self._poll_source: SourceCodes | None = None

    async def start(self) -> None:
        # pylint: disable=protected-access
        self._client._listen.add(self._listen)
        self._client.register_update_provider(self.get_update_tasks)

    async def stop(self) -> None:
        # pylint: disable=protected-access
        self._client._listen.remove(self._listen)
        self._client.unregister_update_provider(self.get_update_tasks)

    async def __aenter__(self) -> "State":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    def to_dict(self) -> dict[str, Any]:
        return {
            "POWER": self.get_power(),
            "VOLUME": self.get_volume(),
            "SOURCE": self.get_source(),
            "MUTE": self.get_mute(),
            "HEADPHONES": self.get_headphones(),
            "MENU": self.get_menu(),
            "DISPLAY_INFO_TYPE": self.get_display_info_type(),
            "IMAX_ENHANCED": self.get_imax_enhanced(),
            "INCOMING_VIDEO_PARAMETERS": self.get_incoming_video_parameters(),
            "INCOMING_AUDIO_FORMAT": self.get_incoming_audio_format(),
            "INCOMING_AUDIO_SAMPLE_RATE": self.get_incoming_audio_sample_rate(),
            "DECODE_MODE_2CH": self.get_decode_mode_2ch(),
            "DECODE_MODE_MCH": self.get_decode_mode_mch(),
            "ROOM_EQUALIZATION": self.get_room_equalization(),
            "ROOM_EQ_NAMES": self.get_room_eq_names(),
            "DOLBY_AUDIO": self.get_dolby_audio(),
            "BASS_EQUALIZATION": self.get_bass_equalization(),
            "TREBLE_EQUALIZATION": self.get_treble_equalization(),
            "BALANCE": self.get_balance(),
            "LIPSYNC_DELAY": self.get_lipsync_delay(),
            "SUBWOOFER_TRIM": self.get_subwoofer_trim(),
            "SUB_STEREO_TRIM": self.get_sub_stereo_trim(),
            "COMPRESSION": self.get_compression(),
            "GENERAL_SETUP": self.get_general_setup(),
            "DAB_STATION": self.get_dab_station(),
            "DLS_PDT": self.get_dls_pdt(),
            "RDS_INFORMATION": self.get_rds_information(),
            "TUNER_PRESET": self.get_tuner_preset(),
            "PRESET_DETAIL": self.get_preset_details(),
            "NETWORK_PLAYBACK_STATUS": self.get_network_playback_status(),
            "NOW_PLAYING": self.get_now_playing(),
            "BLUETOOTH_STATUS": self.get_bluetooth_status(),
        }

    def __repr__(self) -> str:
        return "State ({}) Amx ({})".format(
            self.to_dict(), self._amxduet.values if self._amxduet else {}
        )

    def _listen(self, packet: ResponsePacket | AmxDuetResponse) -> None:
        if isinstance(packet, AmxDuetResponse):
            self._amxduet = packet
            return

        if packet.zn != self._zn:
            return

        if packet.ac == AnswerCodes.STATUS_UPDATE:
            self._state[packet.cc] = packet.data
        else:
            self._state[packet.cc] = None
        self._changed.set()

    @property
    def zn(self) -> int:
        return self._zn

    @property
    def client(self) -> Client:
        return self._client

    @property
    def model(self) -> str | None:
        if self._amxduet:
            return self._amxduet.device_model
        return None

    @property
    def revision(self) -> str | None:
        if self._amxduet:
            return self._amxduet.device_revision
        return None

    @property
    def _api_model(self) -> ApiModel:
        return api_model_for(self.model)

    def _is_command_supported(self, cc: CommandCodes) -> bool:
        """Check if a command is supported by the current device."""
        if cc in self._unsupported_commands:
            return False
        if cc.version is not None and self.model is not None:
            return self.model in cc.version
        return True

    def _is_command_supported_on_source(self, cc: CommandCodes) -> bool:
        """True iff `cc` has no source gate, the gate is satisfied, or the current source is unknown."""
        if cc.sources is None:
            return True
        src = self.get_source()
        return src is None or src in cc.sources

    def _should_update(self, cc: CommandCodes) -> bool:
        """Whether the update loop may fetch this command in the current state.

        When it is due is decided by the polling tiers in _due_commands.
        """
        if not self._is_command_supported(cc):
            return False
        if not (cc.flags & CommandFlags.ZONE_SUPPORT) and self._zn != 1:
            return False
        if not (cc.flags & CommandFlags.UPDATE):
            return False
        if not self._is_command_supported_on_source(cc):
            return False
        return True

    def _forget_connection(self) -> None:
        """Drop what was learnt on a previous connection, for a fresh first pass."""
        self._state = dict()
        self._now_playing = None
        self._updated.clear()
        self._poll_due = {}
        self._poll_power = None
        self._poll_source = None

    def _due_commands(self) -> tuple[list[CommandCodes], set[_PollTier], bool]:
        """Pick the commands to read on this pass of the update loop.

        Returns the commands, the tiers they were picked for, and whether this
        pass follows a power-on (the tiers then come round again after the
        settling time rather than their usual interval).

        The first pass on a connection reads everything, and so does the pass
        after the zone powers on or changes source: per-input settings such as
        tone and room EQ follow the source, and source-specific information
        starts to apply. Otherwise each tier is read when its interval has
        passed, and a zone in standby reads nothing but its power.
        """
        now = _monotonic()
        power = self.get_power()
        source = self.get_source()
        first_pass = not self._updated.is_set()
        powered_on = power is True and self._poll_power is False
        source_changed = (
            source is not None
            and self._poll_source is not None
            and source != self._poll_source
        )
        # An unknown value keeps the last known one, so a change is still
        # noticed after a read that failed in between.
        if power is not None:
            self._poll_power = power
        if source is not None:
            self._poll_source = source

        refresh = first_pass or powered_on or source_changed
        if refresh:
            due = set(_PollTier)
        else:
            due = {
                tier for tier in _PollTier if now >= self._poll_due.get(tier, 0.0)
            }
        if not due:
            return [], due, powered_on

        commands: list[CommandCodes] = []
        for cc in CommandCodes:
            if not self._should_update(cc):
                continue
            if power is False and not first_pass and cc != CommandCodes.POWER:
                continue
            if cc in _POLL_ONCE_COMMANDS and cc in self._state:
                continue
            if cc == CommandCodes.PRESET_DETAIL and not refresh:
                # Fifty requests, for a list that only changes when the user
                # stores a preset: read when the tuner is selected, not on the
                # safety-net cycle.
                continue
            if _poll_tier(cc) in due:
                commands.append(cc)
        return commands, due, powered_on

    def _finish_pass(self, tiers: set[_PollTier], settle: bool) -> None:
        """Set when the given tiers are next due, counting from now.

        Also takes the power and source the pass read as the baseline for
        noticing a change, if none was known yet. A known baseline is left
        alone, so a change that arrived during the pass is still noticed.
        """
        now = _monotonic()
        for tier in tiers:
            interval = _POLL_POWER_ON_SETTLE if settle else _POLL_INTERVALS[tier]
            self._poll_due[tier] = now + interval.total_seconds()
        if self._poll_power is None:
            self._poll_power = self.get_power()
        if self._poll_source is None:
            self._poll_source = self.get_source()

    def _is_value_supported(self, value: Any) -> bool:
        """Check per-value model gating (IntOrTypeEnum.version)."""
        version = getattr(value, "version", None)
        if version is not None and self.model is not None:
            return self.model in version
        return True

    def _require_command(self, cc: CommandCodes) -> None:
        """Raise UnsupportedCommand if the command is not supported."""
        if not self._is_command_supported(cc):
            raise UnsupportedCommand(cc=cc, model=self.model)

    async def _request(self, zn: int, cc: CommandCodes, data: bytes, priority: int = 0) -> bytes:
        """Check command support, then send a request."""
        self._require_command(cc)
        try:
            return await self._client.request(zn, cc, data, priority)
        except CommandNotRecognised:
            _LOGGER.debug("Command not recognised, marking %s as unsupported", cc)
            self._unsupported_commands.add(cc)
            raise

    def get_rc5code(
        self, table: dict[tuple[ApiModel, int], dict[_T, bytes]], value: _T
    ) -> bytes:
        lookup = table.get((self._api_model, self._zn))
        if not lookup:
            raise ValueError(
                "Unkown mapping for model {} and zone {}".format(
                    self._api_model, self._zn
                )
            )

        command = lookup.get(value)
        if not command:
            raise ValueError(
                "Unkown command for model {} and zone {} and value {}".format(
                    self._api_model, self._zn, value
                )
            )
        return command

    async def _send_rc5_code(self, code: bytes) -> bool:
        """Send one simulated RC5 command and return whether it was echoed.

        No echo is normal: the SDR-35 carries every RC5 command out without
        echoing it, and the status message the command causes is the only
        confirmation. The client never sends an RC5 frame twice, since a
        repeated relative code (volume, a tone step, a toggle) would act twice.
        """
        try:
            await self._request(
                self._zn, CommandCodes.SIMULATE_RC5_IR_COMMAND, code
            )
        except TimeoutError:
            return False
        return True

    async def _send_rc5(self, table: dict, value) -> bool:
        return await self._send_rc5_code(self.get_rc5code(table, value))

    async def _send_rc5_expecting(
        self, table: dict, value, cc: CommandCodes, expected: bytes
    ) -> None:
        """Send an RC5 command and confirm the zone reports ``expected`` for ``cc``.

        The SDR-35 executes RC5 codes (mute, source, decode mode, power)
        without echoing the simulate-IR frame; only the resulting status push
        follows. Without an echo, wait for that push (or read the value), and
        raise TimeoutError if the zone never reports the expected state.
        """
        if await self._send_rc5(table, value):
            return
        if not await self._wait_for(cc, expected, _CONFIRM_TIMEOUT):
            raise TimeoutError(f"Zone {self._zn} did not report {cc.name} as expected")

    def get(self, cc):
        return self._state[cc]

    def get_incoming_video_parameters(self) -> VideoParameters | None:
        value = self._state.get(CommandCodes.INCOMING_VIDEO_PARAMETERS)
        if value is None:
            return None
        return VideoParameters.from_bytes(value)

    def get_incoming_audio_format(
        self,
    ) -> tuple[IncomingAudioFormat, IncomingAudioConfig] | tuple[None, None]:
        value = self._state.get(CommandCodes.INCOMING_AUDIO_FORMAT)
        if value is None or len(value) != 2:
            return None, None
        return (
            IncomingAudioFormat.from_int(value[0]),
            IncomingAudioConfig.from_int(value[1]),
        )

    def get_incoming_audio_sample_rate(self) -> int | None:
        value = _get_byte(self._state.get(CommandCodes.INCOMING_AUDIO_SAMPLE_RATE))
        if value is None:
            return None
        return SAMPLE_RATE_MAP.get(value, 0)

    def get_decode_mode_2ch(self) -> DecodeMode2CH | None:
        value = _get_byte(self._state.get(CommandCodes.DECODE_MODE_STATUS_2CH))
        if value is None:
            return None
        return DecodeMode2CH.from_int(value)

    async def set_decode_mode_2ch(self, mode: DecodeMode2CH) -> None:
        await self._send_rc5_expecting(
            RC5CODE_DECODE_MODE_2CH,
            mode,
            CommandCodes.DECODE_MODE_STATUS_2CH,
            bytes([int(mode)]),
        )

    def get_decode_mode_mch(self) -> DecodeModeMCH | None:
        value = _get_byte(self._state.get(CommandCodes.DECODE_MODE_STATUS_MCH))
        if value is None:
            return None
        return DecodeModeMCH.from_int(value)

    async def set_decode_mode_mch(self, mode: DecodeModeMCH) -> None:
        await self._send_rc5_expecting(
            RC5CODE_DECODE_MODE_MCH,
            mode,
            CommandCodes.DECODE_MODE_STATUS_MCH,
            bytes([int(mode)]),
        )

    def get_2ch(self) -> bool:
        """Return if source is 2 channel or not."""
        audio_format, audio_config = self.get_incoming_audio_format()
        if audio_format not in (
            IncomingAudioFormat.PCM,
            IncomingAudioFormat.ANALOGUE_DIRECT,
            IncomingAudioFormat.UNDETECTED,
            None,
        ):
            return False
        # Multi-channel PCM exists (observed on an SDR-35 playing 7.1 PCM: the
        # 2ch decode query answers 0x85 while the MCH one answers normally), so
        # use the channel configuration to catch it when the device reports one.
        return audio_config is None or audio_config in _TWO_CHANNEL_CONFIGS

    def get_decode_mode(self) -> DecodeModeMCH | DecodeMode2CH | None:
        if self.get_2ch():
            return self.get_decode_mode_2ch()
        else:
            return self.get_decode_mode_mch()

    def get_decode_modes(
        self,
    ) -> list[DecodeModeMCH] | list[DecodeMode2CH] | None:
        if self.get_2ch():
            modes_2ch = RC5CODE_DECODE_MODE_2CH.get((self._api_model, self._zn), {})
            return [mode for mode in modes_2ch if self._is_value_supported(mode)]
        else:
            modes_mch = RC5CODE_DECODE_MODE_MCH.get((self._api_model, self._zn), {})
            return [mode for mode in modes_mch if self._is_value_supported(mode)]

    async def set_decode_mode(self, mode: str | DecodeModeMCH | DecodeMode2CH) -> None:
        if self.get_2ch():
            if isinstance(mode, str):
                mode = DecodeMode2CH[mode]
            elif not isinstance(mode, DecodeMode2CH):
                raise ValueError("Decode mode not supported at this time")
            await self.set_decode_mode_2ch(mode)
        else:
            if isinstance(mode, str):
                mode = DecodeModeMCH[mode]
            elif not isinstance(mode, DecodeModeMCH):
                raise ValueError("Decode mode not supported at this time")
            await self.set_decode_mode_mch(mode)

    async def set_direct_mode(self, on: bool) -> None:
        await self._send_rc5(RC5CODE_DIRECT_MODE, on)

    def get_power(self) -> bool | None:
        value = _get_byte(self._state.get(CommandCodes.POWER))
        if value is None:
            return None
        return value == 0x01

    async def set_power(self, power: bool) -> None:
        if self._api_model in POWER_WRITE_SUPPORTED:
            bool_to_hex = 0x01 if power else 0x00
            if not power:
                self._state[CommandCodes.POWER] = bytes([0])
            await self._request(self._zn, CommandCodes.POWER, bytes([bool_to_hex]))
        else:
            if power:
                await self._send_rc5_expecting(
                    RC5CODE_POWER, power, CommandCodes.POWER, bytes([0x01])
                )
            else:
                # seed with a response, since device might not
                # respond in timely fashion, so let's just
                # assume we succeded until response come
                # back.
                self._state[CommandCodes.POWER] = bytes([0])
                await self._send_rc5_expecting(
                    RC5CODE_POWER, power, CommandCodes.POWER, bytes([0x00])
                )

    def get_menu(self) -> MenuCodes | None:
        value = _get_byte(self._state.get(CommandCodes.MENU))
        if value is None:
            return None
        return MenuCodes.from_int(value)

    def get_mute(self) -> bool | None:
        value = _get_byte(self._state.get(CommandCodes.MUTE))
        if value is None:
            return None
        return value == 0

    async def set_mute(self, mute: bool) -> None:
        if self._api_model in MUTE_WRITE_SUPPORTED:
            bool_to_hex = 0x00 if mute else 0x01
            await self._request(self._zn, CommandCodes.MUTE, bytes([bool_to_hex]))
        else:
            await self._send_rc5_expecting(
                RC5CODE_MUTE,
                mute,
                CommandCodes.MUTE,
                bytes([0x00]) if mute else bytes([0x01]),
            )

    def get_headphones(self) -> bool | None:
        """Return whether headphones are connected."""
        value = _get_byte(self._state.get(CommandCodes.HEADPHONES))
        if value is None:
            return None
        return value == 0x01

    def get_display_info_type(self) -> int | None:
        """Return the current display information type."""
        return _get_byte(self._state.get(CommandCodes.DISPLAY_INFORMATION_TYPE))

    async def set_display_info_type(self, info_type: int) -> None:
        """Set the display information type. Use 0xE0 to cycle."""
        await self._request(self._zn, CommandCodes.DISPLAY_INFORMATION_TYPE, bytes([info_type]))

    async def set_display_brightness(self, level: DisplayBrightness) -> None:
        await self._send_rc5(RC5CODE_DISPLAY_BRIGHTNESS, level)

    def get_lipsync_delay(self) -> int | None:
        """Return lip sync delay in milliseconds (0-250ms in 5ms steps)."""
        data = self._state.get(CommandCodes.LIPSYNC_DELAY)
        return _get_scaled_negative(data, 0.0, 250.0, 5.0)

    async def set_lipsync_delay(self, delay_ms: int) -> None:
        """Set lip sync delay in milliseconds (0-250ms in 5ms steps)."""
        byte_val = _set_scaled(delay_ms, 0.0, 250.0, 5.0)
        await self._request(self._zn, CommandCodes.LIPSYNC_DELAY, bytes([byte_val]))

    async def inc_lipsync_delay(self) -> None:
        await self._send_rc5(RC5CODE_LIPSYNC, True)

    async def dec_lipsync_delay(self) -> None:
        await self._send_rc5(RC5CODE_LIPSYNC, False)

    def get_subwoofer_trim(self) -> float | None:
        """Return subwoofer trim level in dB (-10 to +10 dB in 0.5dB steps)."""
        data = self._state.get(CommandCodes.SUBWOOFER_TRIM)
        return _get_scaled_negative(data, -10.0, 10.0, 0.5)

    async def set_subwoofer_trim(self, trim_db: float) -> None:
        """Set subwoofer trim level in dB (-10 to +10 dB in 0.5dB steps)."""
        byte_val = _set_scaled(trim_db, -10.0, 10.0, 0.5)
        await self._request(self._zn, CommandCodes.SUBWOOFER_TRIM, bytes([byte_val]))

    async def inc_subwoofer_trim(self) -> None:
        await self._send_rc5(RC5CODE_SUB_TRIM, True)

    async def dec_subwoofer_trim(self) -> None:
        await self._send_rc5(RC5CODE_SUB_TRIM, False)

    def get_sub_stereo_trim(self) -> float | None:
        """Return sub stereo trim level in dB (0 to -10 dB in 0.5dB steps)."""
        data = self._state.get(CommandCodes.SUB_STEREO_TRIM)
        return _get_scaled_negative(data, -10.0, 0.0, 0.5)

    async def set_sub_stereo_trim(self, trim_db: float) -> None:
        """Set sub stereo trim level in dB (0 to -10 dB in 0.5dB steps)."""
        byte_val = _set_scaled(trim_db, -10.0, 0.0, 0.5)
        await self._request(self._zn, CommandCodes.SUB_STEREO_TRIM, bytes([byte_val]))

    def get_treble_equalization(self) -> float | None:
        """Return treble equalization level in dB (-12 to +12 dB in 1dB steps)."""
        data = self._state.get(CommandCodes.TREBLE_EQUALIZATION)
        return _get_scaled_negative(data, -12.0, 12.0, 1.0)

    async def set_treble_equalization(self, trim_db: float) -> None:
        """Set treble equalization level in dB (-12 to +12 dB in 1dB steps)."""
        byte_val = _set_scaled(trim_db, -12.0, 12.0, 1.0)
        await self._request(self._zn, CommandCodes.TREBLE_EQUALIZATION, bytes([byte_val]))

    async def inc_treble_equalization(self) -> None:
        await self._send_rc5(RC5CODE_TREBLE, True)

    async def dec_treble_equalization(self) -> None:
        await self._send_rc5(RC5CODE_TREBLE, False)

    def get_bass_equalization(self) -> float | None:
        """Return bass equalization level in dB (-12 to +12 dB in 1dB steps)."""
        data = self._state.get(CommandCodes.BASS_EQUALIZATION)
        return _get_scaled_negative(data, -12.0, 12.0, 1.0)

    async def set_bass_equalization(self, trim_db: float) -> None:
        """Set bass equalization level in dB (-12 to +12 dB in 1dB steps)."""
        byte_val = _set_scaled(trim_db, -12.0, 12.0, 1.0)
        await self._request(self._zn, CommandCodes.BASS_EQUALIZATION, bytes([byte_val]))

    async def inc_bass_equalization(self) -> None:
        await self._send_rc5(RC5CODE_BASS, True)

    async def dec_bass_equalization(self) -> None:
        await self._send_rc5(RC5CODE_BASS, False)

    def get_room_equalization(self) -> RoomEqMode | None:
        """Return room equalization (DIRAC) mode."""
        value = _get_byte(self._state.get(CommandCodes.ROOM_EQUALIZATION))
        if value is None:
            return None
        return RoomEqMode.from_int(value)

    async def set_room_equalization(self, mode: RoomEqMode) -> None:
        """Set room equalization (DIRAC) mode."""
        await self._request(self._zn, CommandCodes.ROOM_EQUALIZATION, bytes([mode]))

    def get_room_eq_names(self) -> list[str] | None:
        """Return user-defined names for the room EQ profiles."""
        value = self._state.get(CommandCodes.ROOM_EQ_NAMES)
        if value is None:
            return None
        names = []
        for i in range(0, len(value), 20):
            name = value[i:i + 20].decode("ascii", errors="replace").rstrip("\x00").strip()
            names.append(name)
        return names

    def get_dolby_audio(self) -> DolbyAudioMode | None:
        """Return the current Dolby Audio mode."""
        value = _get_byte(self._state.get(CommandCodes.DOLBY_AUDIO))
        if value is None:
            return None
        return DolbyAudioMode.from_int(value)

    async def set_dolby_audio(self, mode: DolbyAudioMode) -> None:
        """Set the Dolby Audio mode."""
        await self._request(self._zn, CommandCodes.DOLBY_AUDIO, bytes([mode]))

    async def inc_dolby_pliix_centre_width(self) -> None:
        await self._send_rc5(RC5CODE_DOLBY_PLIIX_CENTRE_WIDTH, True)

    async def dec_dolby_pliix_centre_width(self) -> None:
        await self._send_rc5(RC5CODE_DOLBY_PLIIX_CENTRE_WIDTH, False)

    async def inc_dolby_pliix_dimension(self) -> None:
        await self._send_rc5(RC5CODE_DOLBY_PLIIX_DIMENSION, True)

    async def dec_dolby_pliix_dimension(self) -> None:
        await self._send_rc5(RC5CODE_DOLBY_PLIIX_DIMENSION, False)

    async def set_dolby_pliix_panorama(self, on: bool) -> None:
        await self._send_rc5(RC5CODE_DOLBY_PLIIX_PANORAMA, on)

    def get_balance(self) -> float | None:
        """Return balance level (-6 to +6 in 1dB steps)."""
        data = self._state.get(CommandCodes.BALANCE)
        return _get_scaled_negative(data, -6.0, 6.0, 1.0)

    async def set_balance(self, value: float) -> None:
        """Set balance level (-6 to +6 in 1dB steps)."""
        byte_val = _set_scaled(value, -6.0, 6.0, 1.0)
        await self._request(self._zn, CommandCodes.BALANCE, bytes([byte_val]))

    async def inc_balance(self) -> None:
        """Shift balance right."""
        await self._send_rc5(RC5CODE_BALANCE, True)

    async def dec_balance(self) -> None:
        """Shift balance left."""
        await self._send_rc5(RC5CODE_BALANCE, False)

    def get_general_setup(self) -> GeneralSetup | None:
        """Return the decoded general-setup block (HDA/JBL series).

        Carries the user-assigned name of the current input plus incoming
        bitrate/dialnorm and installer settings not available elsewhere.
        """
        value = self._state.get(CommandCodes.GENERAL_SETUP)
        if value is None:
            return None
        return GeneralSetup.from_bytes(value)

    async def get_engineering_menu(self) -> EngineeringMenuInfo | None:
        """Query the engineering menu (region, firmware versions).

        Request-only rather than part of the update loop: the data is
        static per boot. Decoding only — the writable side of 0x33
        includes factory reset, which this library does not expose.
        """
        try:
            data = await self._request(
                self._zn, CommandCodes.ENGINEERING_MENU_INFO, bytes([0xF0])
            )
            return EngineeringMenuInfo.from_bytes(data)
        except UnsupportedCommand:
            raise
        except ResponseException as e:
            _LOGGER.warning("Failed to get engineering menu: %s", e.ac)
            return None

    def get_compression(self) -> CompressionMode | None:
        """Return the dynamic range compression setting."""
        value = _get_byte(self._state.get(CommandCodes.COMPRESSION))
        if value is None:
            return None
        return CompressionMode.from_int(value)

    async def set_compression(self, mode: CompressionMode) -> None:
        """Set the dynamic range compression setting."""
        await self._request(self._zn, CommandCodes.COMPRESSION, bytes([mode]))

    def get_imax_enhanced(self) -> ImaxEnhancedMode | None:
        """Return the IMAX Enhanced mode (HDA premium series)."""
        value = _get_byte(self._state.get(CommandCodes.IMAX_ENHANCED))
        if value is None:
            return None
        return ImaxEnhancedMode.from_int(value)

    async def set_imax_enhanced(self, mode: ImaxEnhancedMode) -> None:
        """Set the IMAX Enhanced mode (HDA premium series)."""
        command_byte = IMAX_ENHANCED_SET_MAP[mode]
        await self._request(self._zn, CommandCodes.IMAX_ENHANCED, bytes([command_byte]))

    def get_video_selection(self) -> VideoSelection | None:
        """Return the video input selection (pre-HDA AVR series)."""
        value = _get_byte(self._state.get(CommandCodes.VIDEO_SELECTION))
        if value is None:
            return None
        return VideoSelection.from_int(value)

    async def set_video_selection(self, mode: VideoSelection) -> None:
        """Set the video input selection (pre-HDA AVR series)."""
        await self._request(
            self._zn, CommandCodes.VIDEO_SELECTION, bytes([mode])
        )

    async def set_hdmi_output(self, output: HdmiOutput) -> None:
        await self._send_rc5(RC5CODE_HDMI_OUTPUT, output)

    def get_source(self) -> SourceCodes | None:
        value = self._state.get(CommandCodes.CURRENT_SOURCE)
        if value is None:
            return None
        try:
            return SourceCodes.from_bytes(value, self._api_model, self._zn)
        except ValueError:
            return None

    def get_source_list(self) -> list[SourceCodes]:
        return list(RC5CODE_SOURCE.get((self._api_model, self._zn), {}).keys())

    async def get_input_name(self) -> str | None:
        """Query the user-configured input name for the current source."""
        try:
            data = await self._request(self._zn, CommandCodes.INPUT_NAME, bytes([0xF0]))
            return data.decode('utf-8', errors='replace').rstrip('\x00').strip()
        except UnsupportedCommand:
            raise
        except Exception as e:
            _LOGGER.warning("Failed to get input name: %s", e)
            return None

    async def set_source(self, src: SourceCodes) -> None:
        """Select a source, powering the zone on first if it is in standby.

        A zone that is waking up can drop the command, so it is sent again
        until the zone reports the source, up to _SOURCE_ATTEMPTS times.
        Raises TimeoutError if the zone never reports it, and ValueError for
        a source this model does not have, before anything is sent.
        """
        expected = src.to_bytes(self._api_model, self._zn)
        if self._api_model not in SOURCE_WRITE_SUPPORTED:
            self.get_rc5code(RC5CODE_SOURCE, src)

        if self.get_power() is False:
            await self._power_on_for_source()

        for attempt in range(1, _SOURCE_ATTEMPTS + 1):
            await self._send_source(src)
            if await self._wait_for(
                CommandCodes.CURRENT_SOURCE, expected, _CONFIRM_TIMEOUT
            ):
                return
            _LOGGER.debug(
                "Zone %s has not switched to %s after attempt %s",
                self._zn, src.name, attempt,
            )
        _LOGGER.warning("Zone %s did not switch to %s", self._zn, src.name)
        raise TimeoutError(f"Zone {self._zn} did not switch to {src.name}")

    async def _power_on_for_source(self) -> None:
        """Power the zone on and wait for it to say so, before a source is sent."""
        try:
            await self.set_power(True)
        except TimeoutError:
            pass  # a zone that is still waking up reports it a little later
        if not await self._wait_for(
            CommandCodes.POWER, bytes([0x01]), _SOURCE_POWER_ON_TIMEOUT
        ):
            _LOGGER.warning(
                "Zone %s did not report power on; selecting the source anyway",
                self._zn,
            )

    async def _send_source(self, src: SourceCodes) -> None:
        """Send one source command, without checking that the zone took it."""
        if self._api_model in SOURCE_WRITE_SUPPORTED:
            self._state[CommandCodes.CURRENT_SOURCE] = await self._request(
                self._zn,
                CommandCodes.CURRENT_SOURCE,
                src.to_bytes(self._api_model, self._zn),
            )
            return
        # Not echoed by this firmware: the status message that follows is
        # what confirms it.
        await self._send_rc5(RC5CODE_SOURCE, src)

    async def _wait_for(
        self, cc: CommandCodes, expected: bytes, timeout: timedelta
    ) -> bool:
        """Wait for the zone to report ``expected`` for ``cc``.

        A status message normally arrives well within the timeout. If none
        has, the value is read once before giving up: the unit sends its
        feedback to whichever client made the latest request, which need not
        be this one.
        """
        try:
            async with asyncio.timeout(timeout.total_seconds()):
                while self._state.get(cc) != expected:
                    self._changed.clear()
                    await self._changed.wait()
            return True
        except TimeoutError:
            pass
        try:
            self._state[cc] = await self._request(self._zn, cc, bytes([0xF0]))
        except (ResponseException, TimeoutError) as e:
            _LOGGER.debug("Could not read %s: %r", cc, e)
            return False
        return self._state[cc] == expected

    def get_volume(self) -> int | None:
        return _get_byte(self._state.get(CommandCodes.VOLUME))

    async def set_volume(self, volume: int) -> None:
        await self._request(self._zn, CommandCodes.VOLUME, bytes([volume]))

    async def inc_volume(self) -> None:
        if self._api_model in VOLUME_STEP_SUPPORTED:
            await self._request(self._zn, CommandCodes.VOLUME, bytes([0xF1]))
        else:
            await self._send_rc5(RC5CODE_VOLUME, True)

    async def dec_volume(self) -> None:
        if self._api_model in VOLUME_STEP_SUPPORTED:
            await self._request(self._zn, CommandCodes.VOLUME, bytes([0xF2]))
        else:
            await self._send_rc5(RC5CODE_VOLUME, False)

    def get_dab_station(self) -> str | None:
        if not self._is_command_supported_on_source(CommandCodes.DAB_STATION):
            return None
        value = self._state.get(CommandCodes.DAB_STATION)
        if value is None:
            return None
        return value.decode("utf8", errors="replace").rstrip()

    def get_dls_pdt(self) -> str | None:
        if not self._is_command_supported_on_source(CommandCodes.DLS_PDT_INFO):
            return None
        value = self._state.get(CommandCodes.DLS_PDT_INFO)
        if value is None:
            return None
        return value.decode("utf8", errors="replace").rstrip()

    def get_rds_information(self) -> str | None:
        if not self._is_command_supported_on_source(CommandCodes.RDS_INFORMATION):
            return None
        value = self._state.get(CommandCodes.RDS_INFORMATION)
        if value is None:
            return None
        return value.decode("utf8", errors="replace").rstrip()

    async def set_tuner_preset(self, preset: int) -> None:
        if not self._is_command_supported_on_source(CommandCodes.TUNER_PRESET):
            return
        await self._request(self._zn, CommandCodes.TUNER_PRESET, bytes([preset]))

    def get_tuner_preset(self) -> int | None:
        if not self._is_command_supported_on_source(CommandCodes.TUNER_PRESET):
            return None
        value = _get_byte(self._state.get(CommandCodes.TUNER_PRESET))
        if value is None or value == 0xFF:
            return None
        return value

    def get_preset_details(self) -> dict[int, PresetDetail] | None:
        if not self._is_command_supported_on_source(CommandCodes.PRESET_DETAIL):
            return None
        return self._presets

    async def send_navigation(self, code: RC5CodeNavigation) -> None:
        await self._send_rc5(RC5CODE_NAVIGATION, code)

    async def send_playback(self, code: RC5CodePlayback) -> None:
        await self._send_rc5(RC5CODE_PLAYBACK, code)

    async def send_toggle(self, code: RC5CodeToggle) -> None:
        await self._send_rc5(RC5CODE_TOGGLE, code)

    async def send_menu_access(self, code: RC5CodeMenuAccess) -> None:
        await self._send_rc5(RC5CODE_MENU_ACCESS, code)

    async def send_numeric(self, digit: int) -> None:
        if not 0 <= digit <= 9:
            raise ValueError(f"Digit must be 0-9, got {digit}")
        if self.model and self.model not in APIVERSION_RC5_NUMERIC_SERIES:
            raise ValueError(
                f"Numeric RC5 not supported on {self.model}"
            )
        await self._send_rc5_code(bytes([0x10, digit]))

    async def send_color(self, color: RC5CodeColor) -> None:
        await self._send_rc5(RC5CODE_COLOR, color)

    async def save_settings(self, pin: tuple[int, int, int, int] = (1, 2, 3, 4)) -> None:
        """Save a secure backup of device settings.

        The PIN defaults to (1, 2, 3, 4), the factory default installer PIN.
        """
        await self._request(
            1, CommandCodes.SAVE_RESTORE_COPY_OF_SETTINGS,
            bytes([SaveRestoreSubCommand.SAVE, *SAVE_RESTORE_CONFIRMATION, *pin]),
        )

    async def restore_settings(self, pin: tuple[int, int, int, int] = (1, 2, 3, 4)) -> None:
        """Restore settings from the secure backup.

        The PIN defaults to (1, 2, 3, 4), the factory default installer PIN.
        Raises CommandInvalidAtThisTime if no backup exists.
        """
        await self._request(
            1, CommandCodes.SAVE_RESTORE_COPY_OF_SETTINGS,
            bytes([SaveRestoreSubCommand.RESTORE, *SAVE_RESTORE_CONFIRMATION, *pin]),
        )

    def get_network_playback_status(self) -> NetworkPlaybackStatus | None:
        """Return the network playback status (stopped/transitioning/playing/paused)."""
        if not self._is_command_supported_on_source(CommandCodes.NETWORK_PLAYBACK_STATUS):
            return None
        value = _get_byte(self._state.get(CommandCodes.NETWORK_PLAYBACK_STATUS))
        if value is None:
            return None
        return NetworkPlaybackStatus.from_int(value)

    def get_now_playing(self) -> NowPlayingInfo | None:
        """Return now-playing metadata (HDA series, NET/BT sources)."""
        if not self._is_command_supported_on_source(CommandCodes.NOW_PLAYING_INFO):
            return None
        return self._now_playing

    def get_bluetooth_status(
        self,
    ) -> tuple[BluetoothAudioStatus, str] | tuple[None, None]:
        """Return Bluetooth audio status and track name (HDA series)."""
        if not self._is_command_supported_on_source(CommandCodes.BLUETOOTH_STATUS):
            return None, None
        value = self._state.get(CommandCodes.BLUETOOTH_STATUS)
        if not value:
            return None, None
        status = BluetoothAudioStatus.from_int(value[0])
        if len(value) > 1:
            track = value[1:].decode("ascii", errors="replace").rstrip("\x00").strip()
        else:
            track = ""
        return status, track

    async def get_update_tasks(self) -> list[UpdateTask]:
        """Return a list of update coroutines for the current device state.
        """
        priority = _UPDATE_PRIORITY

        async def _update(cc: CommandCodes):
            try:
                data = await self._request(self._zn, cc, bytes([0xF0]), priority)
                self._state[cc] = data
            except UnsupportedZone:
                _LOGGER.debug("Unsupported zone %s for %s", self._zn, cc)
            except CommandNotRecognised:
                self._state[cc] = None
            except ResponseException as e:
                _LOGGER.debug("Response error skipping %s - %s", cc, e.ac)
                self._state[cc] = None
            except NotConnectedException as e:
                _LOGGER.debug("Not connected skipping %s", cc)
                self._state[cc] = None
            except TimeoutError:
                # Debug, like every other handler in this block: a slow or
                # sleeping device is expected, and the value is simply left
                # stale until the next pass.
                _LOGGER.debug("Timeout requesting %s", cc)

        async def _update_presets() -> None:
            presets = {}
            for preset in range(1, 51):
                try:
                    data = await self._request(
                        self._zn, CommandCodes.PRESET_DETAIL, bytes([preset]), priority
                    )
                    detail = PresetDetail.from_bytes(data)
                    if detail is not None:
                        presets[preset] = detail
                except CommandInvalidAtThisTime:
                    break
                except CommandNotRecognised:
                    _LOGGER.debug("Presets not supported skipping %s", preset)
                    break
                except NotConnectedException as e:
                    _LOGGER.debug("Not connected skipping preset %s", preset)
                    return
                except TimeoutError:
                    _LOGGER.debug("Timeout requesting preset %s", preset)
                    return
            self._presets = presets

        async def _update_now_playing() -> None:
            kwargs = {}
            for field in attr.fields(NowPlayingInfo):
                if "request" not in field.metadata:
                    continue
                request = field.metadata["request"]
                try:
                    data = await self._request(
                        self._zn, CommandCodes.NOW_PLAYING_INFO, bytes([request]), priority
                    )
                    kwargs[field.name] = field.metadata["converter"](
                        strip_now_playing_echo(request, data)
                    )
                except CommandNotRecognised:
                    _LOGGER.debug("Now playing not supported")
                    self._now_playing = None
                    return
                except ParameterNotRecognised:
                    _LOGGER.debug("Now playing %s not supported", field.name)
                except NotConnectedException:
                    _LOGGER.debug("Not connected skipping now playing")
                    self._now_playing = None
                    return
                except ResponseException as e:
                    _LOGGER.debug("Now playing %s error: %s", field.name, e.ac)
                except TimeoutError:
                    _LOGGER.debug("Timeout requesting now playing %s", field.name)

            if kwargs:
                self._now_playing = NowPlayingInfo(**kwargs)
            else:
                self._now_playing = None

        async def _update_amxduet() -> None:
            try:
                data = await self._client.request_raw(AmxDuetRequest(), priority)
                self._amxduet = data
            except ResponseException as e:
                _LOGGER.debug("Response error skipping %s", e.ac)
            except NotConnectedException as e:
                _LOGGER.debug("Not connected skipping amx")
            except TimeoutError:
                _LOGGER.debug("Timeout requesting amx")

        if not self._client.connected:
            self._forget_connection()
            return []

        # The update loop only runs while connected, so a reconnect is noticed
        # here rather than by a disconnected pass. Whatever was cached before
        # the first pass arrived on this connection and is kept.
        connection = self._client.connection_id
        if self._poll_connection is None:
            self._poll_connection = connection
        elif connection != self._poll_connection:
            self._forget_connection()
            self._poll_connection = connection

        if self._amxduet is None:
            await _update_amxduet()

        first_pass = not self._updated.is_set()
        commands, due, settle = self._due_commands()

        tasks: list[UpdateTask] = []
        for cc in commands:
            if cc == CommandCodes.NOW_PLAYING_INFO:
                tasks.append(_update_now_playing())
            elif cc == CommandCodes.PRESET_DETAIL:
                tasks.append(_update_presets())
            else:
                tasks.append(_update(cc))

        if not tasks:
            self._finish_pass(due, settle)
            if first_pass:
                self._updated.set()
            return []

        async def _run_pass() -> None:
            try:
                await run_tasks(*tasks)
            finally:
                # Counted from the end of the pass, so a slow unit still gets
                # a rest between passes.
                self._finish_pass(due, settle)
                if first_pass:
                    self._updated.set()

        return [_run_pass()]

    async def update(self) -> None:
        """Block until the provider-driven update loop completes one pass."""
        # pylint: disable=protected-access
        await wait_any(self._updated, self._client._disconnected)
        if self._client._disconnected.is_set():
            raise NotConnectedException()
