import enum
import time
import asyncio
import logging
import functools
import threading


class FilterStatus(enum.IntEnum):
    Out = 0
    In = 1
    OpenCircuit = 2
    ShortCircuit = 3


class ShutterStatus(enum.IntEnum):
    Open = 0
    Closed = 1


REQ_HEADER = "!PFCU"
REP_HEADER = "%"
BROADCAST = "ALL"

VALID_MODULES = ["{:02d}".format(i) for i in range(16)] + [BROADCAST]


def syncer(func):
    async def acall(coro):
        return func(await coro)

    @functools.wraps(func)
    def wrapper(arg):
        return acall(arg) if asyncio.iscoroutine(arg) else func(arg)
    return wrapper


def yes_no(text):
    return "YES" in text


class PFCUError(Exception):
    pass


def encode(module, cmd):
    return "{}{} {}\r".format(REQ_HEADER, module, cmd).encode()


def decode(reply):
    reply = reply.decode()
    assert reply.startswith(REP_HEADER)
    pfcu, result, text = reply.split(" ", 2)
    result = "OK" if result == "OK" else "ERROR"
    text = text[:-2].replace("DONE", "").strip().rstrip(";")
    if result == "ERROR":
        raise PFCUError(text)
    return text


@syncer
def decode_status(status):
    return status.replace("\r\n", "\n").replace(";", "").strip()


@syncer
def decode_shutter_status(status):
    status = status.lower()
    if "open" in status:
        return ShutterStatus.Open
    elif "closed" in status:
        return ShutterStatus.Closed
    raise PFCUError("Unexpected reply: {!r}".format(status))


@syncer
def decode_filters_status(status):
    return [FilterStatus(int(channel)) for channel in status]


def sec_to_exposure_decimation(sec):
    decimation = 1
    while (2**16 * decimation * 10E-3) < sec:
        decimation *= 10
    exposure = int(sec / (decimation*10e-3))
    return exposure, decimation


@syncer
def parse_status(status):
    lines = status.split("\n")
    channels = []
    for i in range(4):
        nb, inout, fpanel, ttl, rs232, shorted, open = lines[2+i].split()
        ch = dict(nb=int(nb),in_out=inout, front_panel=fpanel,
                  ttl=ttl, rs232=rs232, shorted=yes_no(shorted), open=yes_no(open))
        channels.append(ch)
    return {
        "id": lines[0],
        "channels": channels,
        "remote_control_enabled": yes_no(lines[-4]),
        "remote_control_only": yes_no(lines[-3]),
        "shutter_enabled": yes_no(lines[-2]),
        "decimation": int(lines[-1].rsplit(" ", 1)[-1])
    }


class BaseProtocol:
    """
    Handles communication protocol
    - latency / back-pressure
    - encode/decode bytes <-> text
    - serializes read calls
    """

    COMMAND_LATENCY = 0.0

    def __init__(self, connection, module=BROADCAST, log=None):
        module = str(module)
        assert module in VALID_MODULES
        self.conn = connection
        self.module = module
        self._last_command = 0
        self._log = log or logging.getLogger('xia_pfcu.{}'.format(type(self).__name__))

    def _wait_time(self):
        return self._last_command + self.COMMAND_LATENCY - time.monotonic()


class AIOProtocol(BaseProtocol):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = asyncio.Lock()

    async def _back_pressure(self):
        wait = self._wait_time()
        if wait > 0:
            await asyncio.sleep(wait)

    async def write_readline(self, data, eol=None):  # aka: query or put_get
        data = encode(self.module, data)
        self._log.debug("write: %r", data)
        await self._back_pressure()
        try:
            async with self._lock:
                # TODO: maybe consume garbage in the buffer ?
                reply = await self.conn.write_readline(data, eol=eol)
            self._log.debug("read: %r", reply)
            return decode(reply)
        finally:
            self._last_command = time.monotonic()


class IOProtocol(BaseProtocol):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()

    def _back_pressure(self):
        wait = self._wait_time()
        if wait > 0:
            time.sleep(wait)

    def write_readline(self, data, eol=None):  # aka: query or put_get
        data = encode(self.module, data)
        self._log.debug("write: %r", data)
        self._back_pressure()
        try:
            with self._lock:
                # TODO: maybe consume garbage in the buffer ?
                reply = self.conn.write_readline(data, eol=eol)
            self._log.debug("read: %r", reply)
            return decode(reply)
        finally:
            self._last_command = time.monotonic()


def Protocol(connection, *args, **kwargs):
    func = connection.write_readline
    klass = AIOProtocol if asyncio.iscoroutinefunction(func) else IOProtocol
    return klass(connection, *args, **kwargs)


def protocol_for_url(url, *args, **kwargs):
    from .connection import connection_for_url
    module = kwargs.pop("module", BROADCAST)
    log = kwargs.pop("log", None)
    conn = connection_for_url(url, *args, **kwargs)
    return Protocol(conn, module=module, log=log)
