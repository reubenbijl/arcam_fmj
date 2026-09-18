import asyncio
import logging
from datetime import timedelta

import pytest
from unittest.mock import MagicMock

from arcam.fmj.client import Client
from arcam.fmj.state import State
from arcam.fmj.codecs import AnswerCodes, SourceCodes
from arcam.fmj.commands import CommandCodes
from arcam.fmj.models import ApiModel
from arcam.fmj.packets import AmxDuetResponse, ResponsePacket
from arcam.fmj.rc5 import RC5CODE_POWER, RC5CODE_SOURCE


MODEL_FOR_API = {
    ApiModel.API450_SERIES: "AVR450",
    ApiModel.API860_SERIES: "AVR850",
    ApiModel.APIHDA_SERIES: "AVR30",
    ApiModel.APISA_SERIES: "SA30",
    ApiModel.APIPA_SERIES: "PA720",
    ApiModel.APIST_SERIES: "ST60",
}


def make_state(client, zn, api_model):
    state = State(client, zn)
    state._amxduet = AmxDuetResponse({"Device-Model": MODEL_FOR_API[api_model]})
    return state


@pytest.mark.parametrize(
    "zn, api_model, source, data",
    [
        (1, ApiModel.API450_SERIES, SourceCodes.AUX, bytes([0x08])),
        (1, ApiModel.API860_SERIES, SourceCodes.AUX, bytes([0x08])),
        (1, ApiModel.APIHDA_SERIES, SourceCodes.AUX, bytes([0x08])),
        (1, ApiModel.APIHDA_SERIES, SourceCodes.UHD, bytes([0x06])),
        (1, ApiModel.APISA_SERIES, SourceCodes.AUX, bytes([0x02])),
        (1, ApiModel.APIST_SERIES, SourceCodes.DIG1, bytes([0x01])),
        (1, ApiModel.APIPA_SERIES, None, bytes([0x02])),
        (2, ApiModel.API450_SERIES, SourceCodes.AUX, bytes([0x08])),
        (2, ApiModel.API860_SERIES, SourceCodes.AUX, bytes([0x08])),
        (2, ApiModel.APIHDA_SERIES, SourceCodes.AUX, bytes([0x08])),
        (2, ApiModel.APIHDA_SERIES, SourceCodes.UHD, bytes([0x06])),
        (2, ApiModel.APISA_SERIES, SourceCodes.AUX, bytes([0x02])),
        (2, ApiModel.APIPA_SERIES, None, bytes([0x02])),
        (2, ApiModel.APIST_SERIES, None, bytes([0x01])),
    ],
)
async def test_get_source(zn, api_model, source, data):
    client = MagicMock(spec=Client)
    state = make_state(client, zn, api_model)
    state._state[CommandCodes.CURRENT_SOURCE] = data

    assert state.get_source() == source


@pytest.mark.parametrize(
    "zn, api_model, source, ir, data",
    [
        (1, ApiModel.API450_SERIES, SourceCodes.AUX, True, bytes([16, 8])),
        (1, ApiModel.API860_SERIES, SourceCodes.AUX, True, bytes([16, 99])),
        (1, ApiModel.APIHDA_SERIES, SourceCodes.AUX, True, bytes([16, 99])),
        (1, ApiModel.APIHDA_SERIES, SourceCodes.UHD, True, bytes([16, 125])),
        (1, ApiModel.APISA_SERIES, SourceCodes.AUX, False, bytes([0x02])),
        (2, ApiModel.API450_SERIES, SourceCodes.AUX, True, bytes([23, 13])),
        (2, ApiModel.API860_SERIES, SourceCodes.AUX, True, bytes([23, 13])),
        (2, ApiModel.APIHDA_SERIES, SourceCodes.AUX, True, bytes([23, 13])),
        (2, ApiModel.APIHDA_SERIES, SourceCodes.UHD, True, bytes([23, 23])),
        (2, ApiModel.APISA_SERIES, SourceCodes.AUX, False, bytes([0x02])),
    ],
)
async def test_set_source(zn, api_model, source, ir, data):
    client = MagicMock(spec=Client)
    state = make_state(client, zn, api_model)
    reported = source.to_bytes(api_model, zn)

    if ir:
        command_code = CommandCodes.SIMULATE_RC5_IR_COMMAND
    else:
        command_code = CommandCodes.CURRENT_SOURCE

    async def request(req_zn, cc, req_data, priority=0):
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            # The unit executes it and pushes the new source.
            state._listen(
                ResponsePacket(
                    req_zn,
                    CommandCodes.CURRENT_SOURCE,
                    AnswerCodes.STATUS_UPDATE,
                    reported,
                )
            )
            return req_data
        assert cc == CommandCodes.CURRENT_SOURCE
        return reported

    client.request.side_effect = request

    await state.set_source(source)

    sent = [
        call.args
        for call in client.request.call_args_list
        if call.args[1] == command_code and call.args[2] == data
    ]
    assert sent == [(zn, command_code, data, 0)]
    assert state.get_source() == source


@pytest.mark.parametrize(
    "zn, api_model, source",
    [
        (1, ApiModel.APIPA_SERIES, SourceCodes.AUX),
        (2, ApiModel.APIPA_SERIES, SourceCodes.AUX),
    ],
)
async def test_set_source_invalid(zn, api_model, source):
    client = MagicMock(spec=Client)
    state = make_state(client, zn, api_model)

    with pytest.raises(ValueError):
        await state.set_source(source)


# --- Selecting a source on a zone that may be asleep -----------------------

HDA = ApiModel.APIHDA_SERIES


@pytest.fixture
def no_wait(monkeypatch):
    """Stop waiting for status pushes at once, so each wait ends in a read."""
    monkeypatch.setattr("arcam.fmj.state._CONFIRM_TIMEOUT", timedelta(0))
    monkeypatch.setattr("arcam.fmj.state._POWER_ON_TIMEOUT", timedelta(0))


class FakeZone:
    """One zone of an HDA unit that never echoes a simulated RC5 command.

    That is how an SDR-35 behaves: the command is carried out and a status
    message follows, but the 0x08 frame itself is never answered. The first
    ``drops`` source commands are ignored, as by a zone that is still waking.
    """

    def __init__(
        self,
        state: State,
        *,
        power: bool,
        source: SourceCodes = SourceCodes.CD,
        drops: int = 0,
        push: bool = True,
        powers_on: bool = True,
        power_report_delay: float = 0.0,
    ) -> None:
        self.state = state
        self.zn = state.zn
        self.power = power
        self.source = source
        self.drops = drops
        self.push = push
        self.powers_on = powers_on
        self.power_report_delay = power_report_delay
        #: Every RC5 code received, in order.
        self.rc5: list[bytes] = []
        state.client.request.side_effect = self.request

    async def request(self, zn, cc, data, priority=0):
        assert zn == self.zn
        if cc == CommandCodes.SIMULATE_RC5_IR_COMMAND:
            self.rc5.append(bytes(data))
            self.execute(bytes(data))
            raise TimeoutError
        if cc == CommandCodes.POWER:
            return bytes([0x01 if self.power else 0x00])
        if cc == CommandCodes.CURRENT_SOURCE:
            return self.source.to_bytes(HDA, self.zn)
        raise AssertionError(f"unexpected request {cc}")

    def execute(self, code: bytes) -> None:
        if code == RC5CODE_POWER[(HDA, self.zn)][True]:
            if not self.powers_on:
                return
            if self.power_report_delay:
                # Reports being on only once it has finished waking.
                asyncio.get_running_loop().call_later(
                    self.power_report_delay, self.power_on
                )
            else:
                self.power_on()
            return
        for source, source_code in RC5CODE_SOURCE[(HDA, self.zn)].items():
            if code != source_code:
                continue
            if self.drops:
                self.drops -= 1
                return
            self.source = source
            self.report(CommandCodes.CURRENT_SOURCE, source.to_bytes(HDA, self.zn))

    def power_on(self) -> None:
        self.power = True
        self.report(CommandCodes.POWER, bytes([0x01]))

    def report(self, cc: CommandCodes, data: bytes) -> None:
        if self.push:
            self.state._listen(
                ResponsePacket(self.zn, cc, AnswerCodes.STATUS_UPDATE, data)
            )


def hda_state(zn: int = 1, *, power: bool | None) -> State:
    state = make_state(MagicMock(spec=Client), zn, HDA)
    if power is not None:
        state._state[CommandCodes.POWER] = bytes([0x01 if power else 0x00])
    return state


def source_code(zn: int, source: SourceCodes) -> bytes:
    return RC5CODE_SOURCE[(HDA, zn)][source]


async def test_set_source_sends_once_when_the_zone_reports_it():
    state = hda_state(power=True)
    zone = FakeZone(state, power=True)

    await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [source_code(1, SourceCodes.BD)]
    assert state.get_source() == SourceCodes.BD


async def test_set_source_reads_back_when_no_push_arrives(no_wait):
    """Feedback may go to another client; a read settles it."""
    state = hda_state(power=True)
    zone = FakeZone(state, power=True, push=False)

    await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [source_code(1, SourceCodes.BD)]
    assert state.get_source() == SourceCodes.BD


async def test_set_source_resends_a_dropped_command(no_wait):
    state = hda_state(power=True)
    zone = FakeZone(state, power=True, drops=1)

    await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [source_code(1, SourceCodes.BD)] * 2
    assert state.get_source() == SourceCodes.BD


async def test_set_source_gives_up_after_three_attempts(no_wait, caplog):
    state = hda_state(power=True)
    zone = FakeZone(state, power=True, drops=10)

    with pytest.raises(TimeoutError):
        await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [source_code(1, SourceCodes.BD)] * 3
    assert "did not switch to BD" in caplog.text


async def test_set_source_powers_a_zone_in_standby_on_first():
    state = hda_state(power=False)
    zone = FakeZone(state, power=False)

    await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [
        RC5CODE_POWER[(HDA, 1)][True],
        source_code(1, SourceCodes.BD),
    ]
    assert state.get_power() is True
    assert state.get_source() == SourceCodes.BD


async def test_set_source_waits_for_a_slow_power_on_report():
    """The source goes out only once the zone has said it is on."""
    state = hda_state(power=False)
    zone = FakeZone(state, power=False, power_report_delay=0.05)

    async with asyncio.timeout(5):
        await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [
        RC5CODE_POWER[(HDA, 1)][True],
        source_code(1, SourceCodes.BD),
    ]
    assert state.get_source() == SourceCodes.BD


async def test_set_source_on_zone_2_powers_on_zone_2_only():
    state = hda_state(2, power=False)
    zone = FakeZone(state, power=False)

    await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [
        RC5CODE_POWER[(HDA, 2)][True],
        source_code(2, SourceCodes.BD),
    ]
    assert all(call.args[0] == 2 for call in state.client.request.call_args_list)


async def test_set_source_still_tries_when_power_on_is_never_reported(
    no_wait, caplog
):
    state = hda_state(power=False)
    zone = FakeZone(state, power=False, powers_on=False, drops=10)

    with pytest.raises(TimeoutError):
        await state.set_source(SourceCodes.BD)

    assert zone.rc5[0] == RC5CODE_POWER[(HDA, 1)][True]
    assert zone.rc5[1:] == [source_code(1, SourceCodes.BD)] * 3
    assert "did not report power on" in caplog.text


async def test_set_source_unknown_to_the_model_sends_nothing():
    """Nothing is powered on for a source that cannot then be selected."""
    client = MagicMock(spec=Client)
    state = make_state(client, 1, ApiModel.APIPA_SERIES)
    state._state[CommandCodes.POWER] = bytes([0x00])

    with pytest.raises(ValueError):
        await state.set_source(SourceCodes.AUX)

    client.request.assert_not_called()


# --- A zone that has only just woken up ------------------------------------
#
# Measured on an SDR-35: after a power-on from Home Assistant the zone reported
# being on within 2.7 s, then ignored a source command sent straight after, at
# each of the four wakes in ten days of history. So while it may still be
# waking, the command is sent more than _SOURCE_ATTEMPTS times.


async def test_set_source_keeps_sending_while_the_zone_wakes(no_wait):
    """Power on, then select a source: the order an automation uses."""
    state = hda_state(power=False)
    zone = FakeZone(state, power=False, drops=4)

    await state.set_power(True)
    await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [
        RC5CODE_POWER[(HDA, 1)][True],
        *[source_code(1, SourceCodes.BD)] * 5,
    ]
    assert state.get_source() == SourceCodes.BD


async def test_set_source_stops_sending_once_the_zone_has_woken(
    no_wait, monkeypatch, caplog
):
    """The extra attempts end _SOURCE_WAKE_TIME after the power-on report."""
    clock = [100.0]
    monkeypatch.setattr("arcam.fmj.state._monotonic", lambda: clock[0])
    state = hda_state(power=False)
    zone = FakeZone(state, power=False, drops=100)
    execute = zone.execute

    def execute_after_a_while(code: bytes) -> None:
        # Each attempt takes about this long on the unit: the echo window,
        # the confirm window and a read.
        clock[0] += 2.6
        execute(code)

    zone.execute = execute_after_a_while

    with pytest.raises(TimeoutError):
        await state.set_source(SourceCodes.BD)

    # Powered on at 102.6; attempts end at 105.2, 107.8, ... 118.2, which
    # is the first past 102.6 + 15.
    assert zone.rc5 == [
        RC5CODE_POWER[(HDA, 1)][True],
        *[source_code(1, SourceCodes.BD)] * 6,
    ]
    assert "did not switch to BD" in caplog.text


async def test_power_reported_on_connecting_is_not_a_wake(no_wait):
    """Only a change from standby to on counts, not the first report."""
    state = hda_state(power=None)
    zone = FakeZone(state, power=True, drops=100)
    zone.report(CommandCodes.POWER, bytes([0x01]))

    with pytest.raises(TimeoutError):
        await state.set_source(SourceCodes.BD)

    assert zone.rc5 == [source_code(1, SourceCodes.BD)] * 3
