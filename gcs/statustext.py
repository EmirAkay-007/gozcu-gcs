"""Reassembling STATUSTEXT messages that do not fit in one packet.

ArduPilot splits a text longer than 50 bytes into chunks that share a non-zero
id and count up in chunk_seq; the chunk holding a NUL byte is the last one,
and it is empty when the text filled the previous chunk exactly
(GCS_Common.cpp, GCS::send_textv). A text that fits in one packet has id 0.

The raw bytes are joined first and decoded as UTF-8 once, so a character split
across two chunks stays whole. pymavlink's own .text decodes every packet as
ASCII, which would turn each non-ASCII letter into a replacement character.
"""

from dataclasses import dataclass, field
from typing import Any

TEXT_LEN = 50  # bytes of text in one STATUSTEXT
INCOMPLETE_AFTER_S = 2.0  # a text whose chunks stop arriving is shown as far as it got
INCOMPLETE_MARK = " [incomplete]"


@dataclass
class _Pending:
    severity: int
    started: float
    chunks: dict[int, bytes] = field(default_factory=dict)  # chunk_seq -> text bytes
    last_seq: int | None = None  # chunk_seq of the final chunk, once it arrived


class StatusTextAssembler:
    def __init__(self) -> None:
        self._pending: dict[tuple[int, int, int], _Pending] = {}

    def add(self, msg: Any, now: float) -> tuple[int, str] | None:
        """Take one STATUSTEXT; returns (severity, text) once a whole text is in."""
        raw: bytes = msg._text_raw  # all 50 bytes; .text is ASCII-decoded
        text, nul, _ = raw.partition(b"\x00")
        if msg.id == 0:
            return msg.severity, decode(text)
        key = (msg.get_srcSystem(), msg.get_srcComponent(), msg.id)
        pending = self._pending.setdefault(key, _Pending(msg.severity, now))
        pending.chunks[msg.chunk_seq] = text
        if nul or len(raw) < TEXT_LEN:
            pending.last_seq = msg.chunk_seq
        if pending.last_seq is None:
            return None
        if any(seq not in pending.chunks for seq in range(pending.last_seq + 1)):
            return None  # an earlier chunk is still missing
        del self._pending[key]
        return pending.severity, decode(join(pending.chunks))

    def expire(self, now: float) -> list[tuple[int, str]]:
        """Texts whose remaining chunks did not arrive in time, marked as incomplete."""
        expired = []
        for key, pending in list(self._pending.items()):
            if now - pending.started >= INCOMPLETE_AFTER_S:
                del self._pending[key]
                expired.append((pending.severity, decode(join(pending.chunks)) + INCOMPLETE_MARK))
        return expired


def join(chunks: dict[int, bytes]) -> bytes:
    return b"".join(chunks[seq] for seq in sorted(chunks))


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")
