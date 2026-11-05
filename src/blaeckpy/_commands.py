"""Parsing <NAME,args> with the #id: prefix, buttons, plain commands, acks."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._properties import MISSING_VALUE, NOT_A_NUMBER, OK, OUT_OF_RANGE

#: Characters a command buffer holds, the terminator counted, so the device list
#: advertises one less: 1023. blaeck's default is 128, a microcontroller's RAM budget that fits a
#: 32-byte text percent-encoded; here a 255-byte text, the most Home Assistant holds, fits even
#: with every byte encoded, along with its name and a #id: prefix.
COMMAND_MAX_CHARS = 1024
PARAM_MAX = 10


class Receiver:
    """Collects bytes into commands: < starts one, > ends it, a second <
    abandons an unfinished one, and what overflows the buffer is dropped and flagged."""

    def __init__(self) -> None:
        self._chars = bytearray()
        self._in_progress = False
        self._overflowed = False
        self._pending = bytearray()
        # How far into _pending has been read. The bytes before it go only once that is most of
        # it: dropping a few from the front moves everything behind them.
        self._read = 0

    def feed(self, data: bytes) -> None:
        self._pending += data

    def has_command(self) -> bool:
        """Whether a complete command is waiting to be taken."""
        p, r = self._pending, self._read
        return p.find(0x3E, r) >= 0 and (self._in_progress or p.find(0x3C, r) >= 0)

    def next_command(self) -> tuple[bytes, bool] | None:
        """The next complete command and whether it overflowed, or None. Bytes after it wait."""
        pending = self._pending
        i, end = self._read, len(pending)
        try:
            while i < end:
                b = pending[i]
                i += 1
                if self._in_progress:
                    if b == 0x3C:
                        self._chars.clear()
                        self._overflowed = False
                    elif b != 0x3E:
                        self._chars.append(b)
                        if len(self._chars) >= COMMAND_MAX_CHARS:
                            del self._chars[COMMAND_MAX_CHARS - 1:]
                            self._overflowed = True
                    else:
                        raw = bytes(self._chars)
                        self._chars.clear()
                        self._in_progress = False
                        return raw, self._overflowed
                elif b == 0x3C:
                    self._in_progress = True
                    self._overflowed = False
            return None
        finally:
            if i >= end:
                pending.clear()
                self._read = 0
            elif i > end // 2:
                del pending[:i]
                self._read = 0
            else:
                self._read = i


@dataclass
class Command:
    raw: bytes
    name: str = ""
    params: list[str] = field(default_factory=list)
    message_id: int = 0
    prefix_len: int = 0
    truncated: bool = False


def _percent_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    hexdigits = b"0123456789abcdefABCDEF"
    while i < len(data):
        if data[i] == 0x25 and i + 2 < len(data) \
                and data[i + 1] in hexdigits and data[i + 2] in hexdigits:
            out.append(int(data[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(data[i])
            i += 1
    # blaeck decodes into a C string, which a decoded NUL ends.
    nul = out.find(0)
    return bytes(out if nul < 0 else out[:nul])


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="surrogateescape")


def parse(raw: bytes, overflowed: bool) -> Command:
    """Splits a command: one #id: prefix at most, the name before the first comma,
    then up to ten parameters, each percent-decoded and then stripped of outer spaces, tabs, CR
    and LF."""
    cmd = Command(raw, truncated=overflowed)
    if not raw:
        return cmd
    p = 0
    if raw[:1] == b"#":
        scan, digits, msg_id = 1, 0, 0
        while scan < len(raw) and 0x30 <= raw[scan] <= 0x39 and digits < 5:
            msg_id = msg_id * 10 + raw[scan] - 0x30
            scan += 1
            digits += 1
        if digits > 0 and raw[scan:scan + 1] == b":" and 0 < msg_id <= 65535:
            cmd.message_id = msg_id
            p = scan + 1
    cmd.prefix_len = p
    rest = raw[p:]
    name, comma, rest = rest.partition(b",")
    if not name:
        return cmd
    cmd.name = _text(name)
    if not comma:
        return cmd
    fields = rest.split(b",")
    if len(fields) > PARAM_MAX:
        fields = fields[:PARAM_MAX]
        cmd.truncated = True
    # Decoded first, then trimmed, so an encoded space at either end goes too.
    cmd.params = [_text(_percent_decode(f).strip(b" \t\r\n")) for f in fields]
    return cmd


def parse_interval(params: list[str]) -> tuple[int, int]:
    """BLAECK.INTERVAL_START's parameter as (ack reason, interval): plain digits, in milliseconds,
    that fit 32 bits. Anything else is refused, and the interval means nothing then."""
    if not params or not params[0]:
        return MISSING_VALUE, 0
    if not (params[0].isascii() and params[0].isdigit()):
        return NOT_A_NUMBER, 0
    interval = int(params[0])
    return (OUT_OF_RANGE, 0) if interval > 0xFFFFFFFF else (OK, interval)
