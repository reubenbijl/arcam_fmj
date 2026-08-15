"""Codec enums, source codes, dataclasses, lookup maps.

Typed values exchanged with the device over the wire. Each enum or struct
corresponds to the data payload of one or more command codes (CC); see the
individual docstrings for the mapping. Definitions are ordered by CC.
"""
from __future__ import annotations

import enum
from typing import Any, Union

import attr

from .models import (
    APIVERSION_AURO_SERIES,
    APIVERSION_AVR_860_ONWARD_SERIES,
    APIVERSION_DOLBY_PL_SERIES,
    APIVERSION_HDA_SERIES,
    APIVERSION_IMAX_SERIES,
    APIVERSION_LOGIC16_SERIES,
    ApiModel,
    IntOrTypeEnum,
)

# --- AC byte (all responses) ---

class AnswerCodes(IntOrTypeEnum):
    """Response status byte (AC) present in every device response.

    See: all specs, "Answer codes" section.
    """

    STATUS_UPDATE = 0x00
    ZONE_INVALID = 0x82
    COMMAND_NOT_RECOGNISED = 0x83
    PARAMETER_NOT_RECOGNISED = 0x84
    COMMAND_INVALID_AT_THIS_TIME = 0x85
    INVALID_DATA_LENGTH = 0x86

# --- CC 0x01: DISPLAY_BRIGHTNESS ---

class DisplayBrightness(IntOrTypeEnum):
    """Front-panel display brightness level.

    Used by DISPLAY_BRIGHTNESS (0x01).

    See: SH289E "Display Brightness (0x01)".
    """

    OFF = 0x00
    L1 = 0x01
    L2 = 0x02

# --- CC 0x06: SAVE_RESTORE_COPY_OF_SETTINGS ---

class SaveRestoreSubCommand(enum.IntEnum):
    """Sub-command for SAVE_RESTORE_COPY_OF_SETTINGS (0x06).

    Data1 selects save (0x00) or restore (0x01); Data2-3 must be the
    confirmation pattern (0x55, 0x55); Data4-7 carry PIN digits.

    See: SH289E "Save/Restore secure copy of settings (0x06)";
         SH256E "Save/Restore secure copy of settings (0x06)".
    """

    SAVE = 0x00
    RESTORE = 0x01

#: Confirmation pattern (Data2-3) required by SAVE_RESTORE_COPY_OF_SETTINGS
#: (0x06). See: SH289E "Save/Restore secure copy of settings (0x06)".
SAVE_RESTORE_CONFIRMATION = bytes([0x55, 0x55])

# --- CC 0x0A: VIDEO_SELECTION ---

class VideoSelection(IntOrTypeEnum):
    """Video input routing for pre-HDA AVRs.

    Used by VIDEO_SELECTION (0x0A).

    See: SH256E "Video selection (0x0A)".
    """

    BD = 0x00
    SAT = 0x01
    AV = 0x02
    PVR = 0x03
    VCR = 0x04
    GAME = 0x05
    STB = 0x06

# --- CC 0x0C: IMAX_ENHANCED ---

class ImaxEnhancedMode(IntOrTypeEnum):
    """IMAX Enhanced processing mode (read values).

    Used by IMAX_ENHANCED (0x0C). The read response uses 0x00-0x02; the
    set command uses different values — see IMAX_ENHANCED_SET_MAP.

    See: SH289E "IMAX Enhanced (0x0C)".
    """

    OFF = 0x00
    ON = 0x01
    AUTO = 0x02

#: Write-side byte values for IMAX_ENHANCED (0x0C). The set command uses
#: 0xF1/F2/F3 rather than the 0x00-0x02 values returned in read responses.
#: See: SH289E "IMAX Enhanced (0x0C)".
IMAX_ENHANCED_SET_MAP: dict[ImaxEnhancedMode, int] = {
    ImaxEnhancedMode.AUTO: 0xF1,
    ImaxEnhancedMode.ON: 0xF2,
    ImaxEnhancedMode.OFF: 0xF3,
}

# --- CC 0x10: DECODE_MODE_STATUS_2CH ---

class DecodeMode2CH(IntOrTypeEnum):
    """Stereo (2-channel) decode mode.

    Used by DECODE_MODE_STATUS_2CH (0x10). HDA adds Auro modes (0x0E-0x10);
    450 series has Dolby PLIIx variants (0x02/0x03/0x05/0x06) instead of
    Dolby Surround (0x04). JBL Synthesis models report 0x0B for their
    exclusive Logic 16 upmixer (listed as "Reserved" in SH289E issue E,
    observed on SDR-35 firmware).

    See: SH289E "Request decode mode status — 2ch (0x10)";
         SH256E "Request decode mode status — 2ch (0x10)".
    """

    STEREO = 0x01
    DOLBY_PLII_IIx_MOVIE = 0x02, APIVERSION_DOLBY_PL_SERIES
    DOLBY_PLII_IIx_MUSIC = 0x03, APIVERSION_DOLBY_PL_SERIES
    DOLBY_SURROUND = 0x04, APIVERSION_AVR_860_ONWARD_SERIES
    DOLBY_PLII_IIx_GAME = 0x05, APIVERSION_DOLBY_PL_SERIES
    DOLBY_PL = 0x06, APIVERSION_DOLBY_PL_SERIES
    DTS_NEO_6_CINEMA = 0x07
    DTS_NEO_6_MUSIC = 0x08
    MCH_STEREO = 0x09

    DTS_NEURAL_X = 0x0A, APIVERSION_AVR_860_ONWARD_SERIES
    LOGIC_16_IMMERSION = 0x0B, APIVERSION_LOGIC16_SERIES
    DTS_VIRTUAL_X = 0x0C, APIVERSION_AVR_860_ONWARD_SERIES

    DOLBY_VIRTUAL_HEIGHT = 0x0D, APIVERSION_HDA_SERIES
    AURO_NATIVE = 0x0E, APIVERSION_AURO_SERIES
    AURO_MATIC_3D = 0x0F, APIVERSION_AURO_SERIES
    AURO_2D = 0x10, APIVERSION_AURO_SERIES

# --- CC 0x11: DECODE_MODE_STATUS_MCH ---

class DecodeModeMCH(IntOrTypeEnum):
    """Multi-channel decode mode.

    Used by DECODE_MODE_STATUS_MCH (0x11). Value 0x03 means Dolby D-EX /
    DTS-ES on the 450 series but DTS Neural:X on 860/HDA.

    See: SH289E "Request Decode mode status — MCH (0x11)";
         SH256E "Request Decode mode status — MCH (0x11)".
    """

    STEREO_DOWNMIX = 0x01
    MULTI_CHANNEL = 0x02

    # This is used for DTS_NEURAL_X on 860 series and HDA series
    DOLBY_D_EX_OR_DTS_ES = 0x03

    DOLBY_PLII_IIx_MOVIE = 0x04, APIVERSION_DOLBY_PL_SERIES
    DOLBY_PLII_IIx_MUSIC = 0x05, APIVERSION_DOLBY_PL_SERIES

    DOLBY_SURROUND = 0x06, APIVERSION_AVR_860_ONWARD_SERIES
    LOGIC_16_IMMERSION = 0x0B, APIVERSION_LOGIC16_SERIES
    DTS_VIRTUAL_X = 0x0C, APIVERSION_AVR_860_ONWARD_SERIES

    DOLBY_VIRTUAL_HEIGHT = 0x0D, APIVERSION_HDA_SERIES
    AURO_NATIVE = 0x0E, APIVERSION_AURO_SERIES
    AURO_MATIC_3D = 0x0F, APIVERSION_AURO_SERIES
    AURO_2D = 0x10, APIVERSION_AURO_SERIES

# --- CC 0x14: MENU ---

class MenuCodes(IntOrTypeEnum):
    """On-screen menu state.

    Used by MENU (0x14).

    See: SH289E "Request menu status (0x14)".
    """

    NONE = 0x00
    SETUP = 0x02
    TRIM = 0x03
    BASS = 0x04
    TREBLE = 0x05
    SYNC = 0x06
    SUB = 0x07
    TUNER = 0x08
    NETWORK = 0x09
    USB = 0x0A

# --- CC 0x1B: PRESET_DETAIL ---

class PresetType(IntOrTypeEnum):
    """Tuner preset type (Data2 of the response).

    Used by PRESET_DETAIL (0x1B). Determines how Data3+ is interpreted:
    FM_FREQUENCY stores MHz/10kHz digits; FM_RDS_NAME and DAB store an
    ASCII station name.

    See: SH256E "Request preset details (0x1B)".
    """

    AM_FREQUENCY = 0x00
    FM_FREQUENCY = 0x01
    FM_RDS_NAME = 0x02
    DAB = 0x03

@attr.s
class PresetDetail:
    """Decoded response for PRESET_DETAIL (0x1B).

    Data1: preset index (1-50). Data2: PresetType. Data3+: frequency digits
    or ASCII station name depending on type.

    See: SH289E "Request preset details (0x1B)";
         SH256E "Request preset details (0x1B)".
    """

    index = attr.ib(type=int)
    type = attr.ib(type=Union[PresetType, int])
    name = attr.ib(type=str)

    @staticmethod
    def from_bytes(data: bytes) -> "PresetDetail":
        type = PresetType.from_int(data[1])
        if type == PresetType.FM_RDS_NAME or type == PresetType.DAB:
            name = data[2:].decode("utf8").rstrip()
        elif type == PresetType.FM_FREQUENCY:
            name = f"{data[2]}.{data[3]:2} MHz"
        elif type == PresetType.AM_FREQUENCY:
            name = f"{data[2]}{data[3]:2} kHz"
        else:
            name = str(data[2:])
        return PresetDetail(data[0], type, name)

# --- CC 0x1C: NETWORK_PLAYBACK_STATUS ---

class NetworkPlaybackStatus(IntOrTypeEnum):
    """Network/USB playback transport state.

    Used by NETWORK_PLAYBACK_STATUS (0x1C).

    See: SH289E "Network playback status (0x1C)".
    """

    STOPPED = 0x00
    TRANSITIONING = 0x01
    PLAYING = 0x02
    PAUSED = 0x03

# --- CC 0x1D: CURRENT_SOURCE ---

class SourceCodes(enum.Enum):
    """Logical input-source identifiers.

    The byte encoding is model-dependent — use ``from_bytes`` / ``to_bytes``
    with a ``(model, zone)`` pair rather than casting directly.
    Used by CURRENT_SOURCE (0x1D) and the RC5 source tables.

    See: SH289E "Request current source (0x1D)";
         SH256E "Request current source (0x1D)".
    """

    FOLLOW_ZONE_1 = enum.auto()
    CD = enum.auto()
    BD = enum.auto()
    AV = enum.auto()
    SAT = enum.auto()
    PVR = enum.auto()
    VCR = enum.auto()
    AUX = enum.auto()
    DISPLAY = enum.auto()
    FM = enum.auto()
    DAB = enum.auto()
    NET = enum.auto()
    USB = enum.auto()
    STB = enum.auto()
    GAME = enum.auto()
    PHONO = enum.auto()
    ARC_ERC = enum.auto()
    UHD = enum.auto()
    BT = enum.auto()
    DIG1 = enum.auto()
    DIG2 = enum.auto()
    DIG3 = enum.auto()
    DIG4 = enum.auto()
    NET_USB = enum.auto()

    @classmethod
    def from_bytes(cls, data: bytes, model: ApiModel, zn: int) -> "SourceCodes":
        try:
            table = SOURCE_CODES[(model, zn)]
        except KeyError:
            raise ValueError(f"Unknown source map for model {model} and zone {zn}")
        for key, value in table.items():
            if value == data:
                return key
        raise ValueError(
            "Unknown source code for model {} and zone {} and value {!r}".format(
                model, zn, data
            )
        )

    def to_bytes(self, model: ApiModel, zn: int):
        try:
            table = SOURCE_CODES[(model, zn)]
        except KeyError:
            raise ValueError(f"Unknown source map for model {model} and zone {zn}")
        if data := table.get(self):
            return data
        raise ValueError(
            "Unknown byte code for model {} and zone {} and value {}".format(
                model, zn, self
            )
        )

#: SourceCodes -> wire byte for 450/860 series (both zones).
#: See: SH256E/SH274E "Request current source (0x1D)".
DEFAULT_SOURCE_MAPPING: dict[SourceCodes, bytes] = {
    SourceCodes.FOLLOW_ZONE_1: bytes([0x00]),
    SourceCodes.CD: bytes([0x01]),
    SourceCodes.BD: bytes([0x02]),
    SourceCodes.AV: bytes([0x03]),
    SourceCodes.SAT: bytes([0x04]),
    SourceCodes.PVR: bytes([0x05]),
    SourceCodes.VCR: bytes([0x06]),
    SourceCodes.AUX: bytes([0x08]),
    SourceCodes.DISPLAY: bytes([0x09]),
    SourceCodes.FM: bytes([0x0B]),
    SourceCodes.DAB: bytes([0x0C]),
    SourceCodes.NET: bytes([0x0E]),
    SourceCodes.USB: bytes([0x0F]),
    SourceCodes.STB: bytes([0x10]),
    SourceCodes.GAME: bytes([0x11]),
    SourceCodes.PHONO: bytes([0x12]),
    SourceCodes.ARC_ERC: bytes([0x13]),
}

#: SourceCodes -> wire byte for HDA series (both zones).
#: Differs from DEFAULT: UHD (0x06) replaces VCR, BT (0x12) replaces PHONO.
#: See: SH289E "Request current source (0x1D)".
HDA_SOURCE_MAPPING: dict[SourceCodes, bytes] = {
    SourceCodes.FOLLOW_ZONE_1: bytes([0x00]),
    SourceCodes.CD: bytes([0x01]),
    SourceCodes.BD: bytes([0x02]),
    SourceCodes.AV: bytes([0x03]),
    SourceCodes.SAT: bytes([0x04]),
    SourceCodes.PVR: bytes([0x05]),
    SourceCodes.UHD: bytes([0x06]),
    SourceCodes.AUX: bytes([0x08]),
    SourceCodes.DISPLAY: bytes([0x09]),
    SourceCodes.FM: bytes([0x0B]),
    SourceCodes.DAB: bytes([0x0C]),
    SourceCodes.NET: bytes([0x0E]),
    SourceCodes.USB: bytes([0x0F]),
    SourceCodes.STB: bytes([0x10]),
    SourceCodes.GAME: bytes([0x11]),
    SourceCodes.BT: bytes([0x12]),
}

#: SourceCodes -> wire byte for SA series. Completely different byte
#: layout from AVR models. See: SH306E "Request current source (0x1D)".
SA_SOURCE_MAPPING: dict[SourceCodes, bytes] = {
    SourceCodes.PHONO: bytes([0x01]),
    SourceCodes.AUX: bytes([0x02]),
    SourceCodes.PVR: bytes([0x03]),
    SourceCodes.AV: bytes([0x04]),
    SourceCodes.STB: bytes([0x05]),
    SourceCodes.CD: bytes([0x06]),
    SourceCodes.BD: bytes([0x07]),
    SourceCodes.SAT: bytes([0x08]),
    SourceCodes.GAME: bytes([0x09]),
    SourceCodes.NET: bytes([0x0B]),
    SourceCodes.USB: bytes([0x0B]),
    SourceCodes.ARC_ERC: bytes([0x0D]),
}

#: SourceCodes -> wire byte for ST60. Digital-only inputs with a
#: combined NET_USB source. See: SH309 "Request current source (0x1D)".
ST_SOURCE_MAPPING: dict[SourceCodes, bytes] = {
    SourceCodes.DIG1: bytes([0x01]),
    SourceCodes.DIG2: bytes([0x02]),
    SourceCodes.DIG3: bytes([0x03]),
    SourceCodes.DIG4: bytes([0x04]),
    SourceCodes.NET_USB: bytes([0x05]),
}

#: Master lookup: (ApiModel, zone) -> source mapping table.
SOURCE_CODES: dict[tuple[ApiModel, int], dict[SourceCodes, bytes]] = {
    (ApiModel.API450_SERIES, 1): DEFAULT_SOURCE_MAPPING,
    (ApiModel.API450_SERIES, 2): DEFAULT_SOURCE_MAPPING,
    (ApiModel.API860_SERIES, 1): DEFAULT_SOURCE_MAPPING,
    (ApiModel.API860_SERIES, 2): DEFAULT_SOURCE_MAPPING,
    (ApiModel.APIHDA_SERIES, 1): HDA_SOURCE_MAPPING,
    (ApiModel.APIHDA_SERIES, 2): HDA_SOURCE_MAPPING,
    (ApiModel.APISA_SERIES, 1): SA_SOURCE_MAPPING,
    (ApiModel.APISA_SERIES, 2): SA_SOURCE_MAPPING,
    (ApiModel.APIST_SERIES, 1): ST_SOURCE_MAPPING,
}

# --- CC 0x37: ROOM_EQUALIZATION ---

class RoomEqMode(IntOrTypeEnum):
    """Room equalisation preset selection.

    Used by ROOM_EQUALIZATION (0x37).

    See: SH289E "Room Equalisation (0x37)".
    """

    OFF = 0x00
    EQ1 = 0x01
    EQ2 = 0x02
    EQ3 = 0x03
    NOT_CALCULATED = 0x04

# --- CC 0x38: DOLBY_AUDIO ---

class DolbyAudioMode(IntOrTypeEnum):
    """Dolby Audio processing mode.

    Used by DOLBY_AUDIO (0x38). Named "Dolby Volume" in the 450/860 specs
    where only OFF/MOVIE ("On") exist; HDA adds MUSIC and NIGHT.

    See: SH289E "Dolby Audio (0x38)";
         SH274E "Dolby Volume (0x38)".
    """

    OFF = 0x00
    MOVIE = 0x01  # "On" on 860 series
    MUSIC = 0x02, APIVERSION_HDA_SERIES
    NIGHT = 0x03, APIVERSION_HDA_SERIES

# --- CC 0x41: COMPRESSION ---

class CompressionMode(IntOrTypeEnum):
    """Dynamic range compression level.

    Used by COMPRESSION (0x41).

    See: SH289E "Compression (0x41)".
    """

    OFF = 0x00
    MEDIUM = 0x01
    HIGH = 0x02

# --- CC 0x42: INCOMING_VIDEO_PARAMETERS ---

class IncomingVideoAspectRatio(IntOrTypeEnum):
    """Detected video aspect ratio (Data7 of INCOMING_VIDEO_PARAMETERS).

    Used by INCOMING_VIDEO_PARAMETERS (0x42).

    See: SH289E "Request incoming video parameters (0x42)".
    """

    UNDEFINED = 0x00
    ASPECT_4_3 = 0x01
    ASPECT_16_9 = 0x02

class IncomingVideoColorspace(IntOrTypeEnum):
    """Detected video HDR colorspace (Data8 of INCOMING_VIDEO_PARAMETERS).

    HDA only — pre-HDA responses are 7 bytes and omit this field.
    Used by INCOMING_VIDEO_PARAMETERS (0x42).

    See: SH289E "Request incoming video parameters (0x42)".
    """

    NORMAL = 0x00
    HDR10 = 0x01
    DOLBY_VISION = 0x02
    HLG = 0x03
    HDR10_PLUS = 0x04

@attr.s
class VideoParameters:
    """Decoded response for INCOMING_VIDEO_PARAMETERS (0x42).

    Data1-2: horizontal resolution (MSB, LSB).
    Data3-4: vertical resolution (MSB, LSB).
    Data5: refresh rate. Data6: interlaced flag.
    Data7: aspect ratio. Data8: colorspace (HDA only, 8-byte response).
    Pre-HDA responses are 7 bytes (DL=0x07) and omit colorspace.

    See: SH289E "Request incoming video parameters (0x42)";
         SH274E "Request incoming video parameters (0x42)".
    """

    horizontal_resolution = attr.ib(type=int)
    vertical_resolution = attr.ib(type=int)
    refresh_rate = attr.ib(type=int)
    interlaced = attr.ib(type=bool)
    aspect_ratio = attr.ib(type=IncomingVideoAspectRatio)
    colorspace = attr.ib(type=IncomingVideoColorspace | None, default=None)

    @staticmethod
    def from_bytes(data: bytes) -> "VideoParameters":
        return VideoParameters(
            horizontal_resolution=int.from_bytes(data[0:2], "big"),
            vertical_resolution=int.from_bytes(data[2:4], "big"),
            refresh_rate=data[4],
            interlaced=(data[5] == 0x01),
            aspect_ratio=IncomingVideoAspectRatio.from_int(data[6]),
            # Colorspace is not reported pre-HDA
            colorspace=IncomingVideoColorspace.from_int(data[7]) if len(data) >= 8 else None,
        )

    def to_bytes(self) -> bytes:
        data = (
            self.horizontal_resolution.to_bytes(2, "big")
            + self.vertical_resolution.to_bytes(2, "big")
            + bytes([
                self.refresh_rate,
                0x01 if self.interlaced else 0x00,
                int(self.aspect_ratio),
            ])
        )
        if self.colorspace is not None:
            data += bytes([int(self.colorspace)])
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizontal_resolution": self.horizontal_resolution,
            "vertical_resolution": self.vertical_resolution,
            "refresh_rate": self.refresh_rate,
            "interlaced": self.interlaced,
            "aspect_ratio": self.aspect_ratio,
            "colorspace": self.colorspace,
        }

# --- CC 0x43: INCOMING_AUDIO_FORMAT ---

class IncomingAudioFormat(IntOrTypeEnum):
    """Detected audio stream codec/format (Data1 of the response).

    Used by INCOMING_AUDIO_FORMAT (0x43). HDA adds Auro 3D (0x19);
    860+ adds Dolby Atmos (0x16), DTS:X (0x17), IMAX Enhanced (0x18).

    See: SH289E "Request incoming audio format (0x43)".
    """

    PCM = 0x00
    ANALOGUE_DIRECT = 0x01
    DOLBY_DIGITAL = 0x02
    DOLBY_DIGITAL_EX = 0x03
    DOLBY_DIGITAL_SURROUND = 0x04
    DOLBY_DIGITAL_PLUS = 0x05
    DOLBY_DIGITAL_TRUE_HD = 0x06
    DTS = 0x07
    DTS_96_24 = 0x08
    DTS_ES_MATRIX = 0x09
    DTS_ES_DISCRETE = 0x0A
    DTS_ES_MATRIX_96_24 = 0x0B
    DTS_ES_DISCRETE_96_24 = 0x0C
    DTS_HD_MASTER_AUDIO = 0x0D
    DTS_HD_HIGH_RES_AUDIO = 0x0E
    DTS_LOW_BIT_RATE = 0x0F
    DTS_CORE = 0x10
    PCM_ZERO = 0x13
    UNSUPPORTED = 0x14
    UNDETECTED = 0x15
    DOLBY_ATMOS = 0x16, APIVERSION_AVR_860_ONWARD_SERIES
    DTS_X = 0x17, APIVERSION_AVR_860_ONWARD_SERIES
    IMAX_ENHANCED = 0x18, APIVERSION_IMAX_SERIES
    AURO_3D = 0x19, APIVERSION_AURO_SERIES

class IncomingAudioConfig(IntOrTypeEnum):
    """Detected audio channel configuration (Data2 of the response).

    Used by INCOMING_AUDIO_FORMAT (0x43) — same command as IncomingAudioFormat;
    Data1 is the format, Data2 is this config. HDA adds Auro layouts (0x30+).

    See: SH289E "Request incoming audio format (0x43)".
    """

    DUAL_MONO = 0x00
    MONO = 0x01
    CENTER_ONLY = 0x01
    STEREO_ONLY = 0x02
    STEREO_SURR_MONO = 0x03
    STEREO_SURR_LR = 0x04
    STEREO_SURR_LR_BACK_MONO = 0x05
    STEREO_SURR_LR_BACK_LR = 0x06
    STEREO_SURR_LR_BACK_MATRIX = 0x07
    STEREO_CENTER = 0x08
    STEREO_CENTER_SURR_MONO = 0x09
    STEREO_CENTER_SURR_LR = 0x0A
    STEREO_CENTER_SURR_LR_BACK_MONO = 0x0B
    STEREO_CENTER_SURR_LR_BACK_LR = 0x0C
    STEREO_CENTER_SURR_LR_BACK_MATRIX = 0x0D
    STEREO_DOWNMIX = 0x0E
    STEREO_ONLY_LO_RO = 0x0F
    DUAL_MONO_LFE = 0x10
    MONO_LFE = 0x11
    CENTER_LFE = 0x11
    STEREO_LFE = 0x12
    STEREO_SURR_MONO_LFE = 0x13
    STEREO_SURR_LR_LFE = 0x14
    STEREO_SURR_LR_BACK_MONO_LFE = 0x15
    STEREO_SURR_LR_BACK_LR_LFE = 0x16
    STEREO_SURR_LR_BACK_MATRIX_LFE = 0x17
    STEREO_CENTER_LFE = 0x18
    STEREO_CENTER_SURR_MONO_LFE = 0x19
    STEREO_CENTER_SURR_LR_LFE = 0x1A
    STEREO_CENTER_SURR_LR_BACK_MONO_LFE = 0x1B
    STEREO_CENTER_SURR_LR_BACK_LR_LFE = 0x1C
    STEREO_CENTER_SURR_LR_BACK_MATRIX_LFE = 0x1D
    STEREO_DOWNMIX_LFE = 0x1E
    STEREO_ONLY_LO_RO_LFE = 0x1F
    UNKNOWN = 0x20
    UNDETECTED = 0x21
    AURO_QUAD = 0x30
    AURO_5_0 = 0x31
    AURO_5_1 = 0x32
    AURO_2_2_2 = 0x33
    AURO_8_0 = 0x34
    AURO_9_1 = 0x35
    AURO_10_1 = 0x36
    AURO_11_1 = 0x37
    AURO_13_1 = 0x38

# --- CC 0x44: INCOMING_AUDIO_SAMPLE_RATE ---

#: Wire byte -> sample rate in Hz (or None for unknown/undetected).
#: Used by INCOMING_AUDIO_SAMPLE_RATE (0x44).
#: See: SH289E "Request incoming audio sample rate (0x44)".
SAMPLE_RATE_MAP: dict[int, int | None] = {
    0x00: 32000,
    0x01: 44100,
    0x02: 48000,
    0x03: 88200,
    0x04: 96000,
    0x05: 176400,
    0x06: 192000,
    0x07: None,  # Unknown
    0x08: None,  # Undetected
}

# --- CC 0x29: GENERAL_SETUP ---
# (Placed after the 0x41-0x44 sections it reuses codecs from.)

#: Wire byte -> incoming bitrate in bits per second, or one of the symbolic
#: rates "open" / "variable" / "lossless". Data14 of GENERAL_SETUP (0x29).
#: See: SH289E "General Setup (0x29)".
INCOMING_BITRATE_MAP: dict[int, int | str] = {
    0x00: 32_000,
    0x01: 56_000,
    0x02: 64_000,
    0x03: 96_000,
    0x04: 112_000,
    0x05: 128_000,
    0x06: 192_000,
    0x07: 224_000,
    0x08: 256_000,
    0x09: 320_000,
    0x0A: 384_000,
    0x0B: 448_000,
    0x0C: 512_000,
    0x0D: 576_000,
    0x0E: 640_000,
    0x0F: 768_000,
    0x10: 960_000,
    0x11: 1_024_000,
    0x12: 1_152_000,
    0x13: 1_280_000,
    0x14: 1_344_000,
    0x15: 1_408_000,
    0x16: 1_411_200,
    0x17: 1_472_000,
    0x18: 1_536_000,
    0x19: 1_920_000,
    0x1A: 2_048_000,
    0x1B: 3_072_000,
    0x1C: 3_840_000,
    0x1D: "open",
    0x1E: "variable",
    0x1F: "lossless",
}

class DisplayOnTime(IntOrTypeEnum):
    """Front-panel display auto-off time (Data29 of GENERAL_SETUP).

    See: SH289E "General Setup (0x29)".
    """

    SECONDS_5 = 0x00
    SECONDS_10 = 0x01
    SECONDS_30 = 0x02
    MINUTE_1 = 0x03
    ALWAYS_ON = 0x04

class ControlOption(IntOrTypeEnum):
    """External control interface setting (Data30 of GENERAL_SETUP).

    See: SH289E "General Setup (0x29)".
    """

    OFF = 0x00
    RS232 = 0x01
    IP = 0x02

class PowerOnOption(IntOrTypeEnum):
    """Behaviour after mains power is applied (Data31 of GENERAL_SETUP).

    See: SH289E "General Setup (0x29)".
    """

    LAST_STATE = 0x00
    STANDBY = 0x01
    ON = 0x02

class MenuLanguage(IntOrTypeEnum):
    """On-screen menu language (Data32 of GENERAL_SETUP).

    See: SH289E "General Setup (0x29)".
    """

    ENGLISH = 0x00
    FRENCH = 0x01
    GERMAN = 0x02
    SPANISH = 0x03
    DUTCH = 0x04
    RUSSIAN = 0x05
    CHINESE = 0x06

def _decode_offset_binary(value: int) -> int:
    """Decode a sign-and-magnitude byte (0x81-0xFF encode -1 downward)."""
    if value >= 0x81:
        return -(value - 0x80)
    return value

@attr.s
class GeneralSetup:
    """Decoded response for GENERAL_SETUP (0x29), HDA/JBL series.

    A 32-byte snapshot of the General Setup menu combined with incoming
    stream information: the user-assigned name of the current input,
    detected audio format/config/rate/bitrate/dialnorm, detected video
    mode, and a handful of installer settings.

    ``bitrate`` is bits per second, or one of the symbolic strings
    "open"/"variable"/"lossless"; ``dialnorm`` is dB (0-31), None when
    out of the documented range (no stream); ``balance`` and
    ``dts_dialogue_control`` follow the documented 0-6 ranges with
    balance negative meaning left.

    See: SH289E "General Setup (0x29)" (JBL issue documents the same
    layout for SDR-35/SDR-38/SDP-55/SDP-58).
    """

    source_name = attr.ib(type=str)
    audio_format = attr.ib(type=IncomingAudioFormat)
    audio_config = attr.ib(type=IncomingAudioConfig)
    sample_rate = attr.ib(type=int | None)
    bitrate = attr.ib(type=int | str | None)
    dialnorm = attr.ib(type=int | None)
    horizontal_resolution = attr.ib(type=int)
    vertical_resolution = attr.ib(type=int)
    refresh_rate = attr.ib(type=int)
    interlaced = attr.ib(type=bool)
    aspect_ratio = attr.ib(type=IncomingVideoAspectRatio)
    colorspace = attr.ib(type=IncomingVideoColorspace)
    compression = attr.ib(type=CompressionMode)
    balance = attr.ib(type=int)
    dts_dialogue_control = attr.ib(type=int)
    max_volume = attr.ib(type=int)
    max_on_volume = attr.ib(type=int)
    display_on_time = attr.ib(type=DisplayOnTime)
    control_option = attr.ib(type=ControlOption)
    power_on_option = attr.ib(type=PowerOnOption)
    language = attr.ib(type=MenuLanguage)

    @staticmethod
    def from_bytes(data: bytes) -> "GeneralSetup":
        if len(data) < 32:
            raise ValueError(f"General setup data too short {data!r}")
        return GeneralSetup(
            source_name=data[0:10].decode("ascii", errors="replace").rstrip("\x00").strip(),
            audio_format=IncomingAudioFormat.from_int(data[10]),
            audio_config=IncomingAudioConfig.from_int(data[11]),
            sample_rate=SAMPLE_RATE_MAP.get(data[12]),
            bitrate=INCOMING_BITRATE_MAP.get(data[13]),
            dialnorm=data[14] if data[14] <= 0x1F else None,
            horizontal_resolution=int.from_bytes(data[15:17], "big"),
            vertical_resolution=int.from_bytes(data[17:19], "big"),
            refresh_rate=data[19],
            interlaced=(data[20] == 0x01),
            aspect_ratio=IncomingVideoAspectRatio.from_int(data[21]),
            colorspace=IncomingVideoColorspace.from_int(data[22]),
            compression=CompressionMode.from_int(data[23]),
            balance=_decode_offset_binary(data[24]),
            dts_dialogue_control=data[25],
            max_volume=data[26],
            max_on_volume=data[27],
            display_on_time=DisplayOnTime.from_int(data[28]),
            control_option=ControlOption.from_int(data[29]),
            power_on_option=PowerOnOption.from_int(data[30]),
            language=MenuLanguage.from_int(data[31]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in attr.fields(GeneralSetup)}

# --- CC 0x33: ENGINEERING_MENU_INFO ---

class DeviceRegion(IntOrTypeEnum):
    """Sales-region setting (Data11 of ENGINEERING_MENU_INFO).

    See: SH289E "Engineering menu (0x33)".
    """

    EUROPE = 0x00
    US = 0x01
    CANADA = 0x02
    AUSTRALIA = 0x03
    CHINA = 0x04

@attr.s
class EngineeringMenuInfo:
    """Decoded response for ENGINEERING_MENU_INFO (0x33), HDA/JBL series.

    Read-only view of the engineering menu: region, remote-code set,
    standby mode, protection status and the various firmware versions.
    The command is also writable on the wire but the writable fields
    include factory reset, so this library deliberately only decodes.

    Field lengths follow SH289E; the trailing version strings are decoded
    defensively since observed responses vary in length between firmware
    revisions.

    See: SH289E "Engineering menu (0x33)".
    """

    region = attr.ib(type=DeviceRegion)
    remote_code = attr.ib(type=int)
    standby_mode = attr.ib(type=int)
    protection_sensitivity = attr.ib(type=int)
    dante_enabled = attr.ib(type=bool)
    c4_sddp_enabled = attr.ib(type=bool)
    shutdown_code = attr.ib(type=int)
    host_version = attr.ib(type=str)
    dsp_version = attr.ib(type=str)
    osd_version = attr.ib(type=str)
    net_version = attr.ib(type=str)

    @staticmethod
    def _version(data: bytes, start: int, end: int) -> str:
        return data[start:end].decode("ascii", errors="replace").rstrip("\x00").strip()

    @staticmethod
    def from_bytes(data: bytes) -> "EngineeringMenuInfo":
        if len(data) < 20:
            raise ValueError(f"Engineering menu data too short {data!r}")
        return EngineeringMenuInfo(
            region=DeviceRegion.from_int(data[10]),
            remote_code=data[11],
            standby_mode=data[12],
            protection_sensitivity=data[13],
            dante_enabled=(data[16] == 0x01),
            c4_sddp_enabled=(data[17] == 0x01),
            shutdown_code=data[19],
            host_version=EngineeringMenuInfo._version(data, 20, 29),
            dsp_version=EngineeringMenuInfo._version(data, 29, 33),
            osd_version=EngineeringMenuInfo._version(data, 33, 37),
            net_version=EngineeringMenuInfo._version(data, 37, 51),
        )

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in attr.fields(EngineeringMenuInfo)}

# --- CC 0x4F: VIDEO_OUTPUT_SWITCHING ---

class HdmiOutput(IntOrTypeEnum):
    """Active HDMI output(s).

    Used by VIDEO_OUTPUT_SWITCHING (0x4F).

    See: SH289E "Set/Request Video Output Switching (0x4F)".
    """

    OUT_1 = 0x02
    OUT_2 = 0x03
    OUT_1_2 = 0x04

# --- CC 0x50: BLUETOOTH_STATUS ---

class BluetoothAudioStatus(IntOrTypeEnum):
    """Bluetooth connection and codec status.

    Used by BLUETOOTH_STATUS (0x50). This command code was "Output Frame Rate"
    on the 450 series (SH256E).

    See: SH289E "Bluetooth status (0x50)".
    """

    NO_CONNECTION = 0x00
    PAUSED = 0x01
    PLAYING_SBC = 0x02
    PLAYING_AAC = 0x03
    PLAYING_APTX = 0x04
    PLAYING_APTX_HD = 0x05

# --- CC 0x64: NOW_PLAYING_INFO ---

class NowPlayingEncoder(IntOrTypeEnum):
    """Audio encoder/codec of the currently playing track.

    Returned by NOW_PLAYING_INFO (0x64) when sub-request is ENCODER (0xF5).

    See: SH289E "Now Playing Information (0x64)".
    """

    MP3 = 0x00
    WAV = 0x01
    WMA = 0x02
    FLAC = 0x03
    ALAC = 0x04
    MQA = 0x05
    UNKNOWN = 0x0A

class NowPlayingRequest(IntOrTypeEnum):
    """Sub-request codes sent as Data1 of NOW_PLAYING_INFO (0x64).

    Each code requests a different field of the currently playing track.

    See: SH289E "Now Playing Information (0x64)".
    """

    TRACK = 0xF0
    ARTIST = 0xF1
    ALBUM = 0xF2
    APPLICATION = 0xF3
    SAMPLE_RATE = 0xF4
    ENCODER = 0xF5

def _decode_string(data: bytes) -> str:
    """Decode a UTF-8 payload, stripping trailing NUL bytes."""
    return data.decode("utf8", errors="replace").rstrip("\x00")

@attr.s
class NowPlayingInfo:
    """Aggregated now-playing metadata from NOW_PLAYING_INFO (0x64).

    Each field corresponds to one NowPlayingRequest sub-code. The ``metadata``
    on each attr carries the sub-request code and a converter for the raw
    response bytes.

    See: SH289E "Now Playing Information (0x64)".
    """

    track = attr.ib(type=str | None, default=None, metadata={"request": NowPlayingRequest.TRACK, "converter": _decode_string})
    artist = attr.ib(type=str | None, default=None, metadata={"request": NowPlayingRequest.ARTIST, "converter": _decode_string})
    album = attr.ib(type=str | None, default=None, metadata={"request": NowPlayingRequest.ALBUM, "converter": _decode_string})
    application = attr.ib(type=str | None, default=None, metadata={"request": NowPlayingRequest.APPLICATION, "converter": _decode_string})
    sample_rate = attr.ib(type=int | None, default=None, metadata={"request": NowPlayingRequest.SAMPLE_RATE, "converter": lambda x: SAMPLE_RATE_MAP.get(x[0], 0)})
    encoder = attr.ib(type=NowPlayingEncoder | None, default=None, metadata={"request": NowPlayingRequest.ENCODER, "converter": lambda x: NowPlayingEncoder.from_int(x[0])})
