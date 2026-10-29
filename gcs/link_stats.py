"""Link quality: packet loss per MAVLink source and arrival rate per message type.

Both describe the link as it is now, over a sliding time window. pymavlink's own
counters cannot: they add up every source on the link and never forget anything.

Not thread-safe: call add() and the queries from one thread and hand other
threads plain numbers.
"""

from collections import deque

SEQ_MODULO = 256
# Sequence numbers wrap at 256, so a step back looks like a big jump forward.
# For jumps of half the range or more, time decides which one it was.
RESYNC_GAP = SEQ_MODULO // 2
# Such a jump right after the previous packet is a duplicate, a late packet or a
# restarted sender; after at least this much silence it is an outage.
OUTAGE_SILENCE_S = 1.0
# Below this much observation, a burst of two messages would read as a huge rate.
MIN_OBSERVATION_S = 1.0


class PacketLossMeter:
    """Packet loss of one MAVLink source, estimated from sequence-number gaps.

    Every sender numbers its packets 0..255 and wraps around, so a gap between
    consecutive numbers means packets went missing. An outage longer than 255
    packets wraps the counter too, so the result is a lower bound; link health
    also watches the heartbeat age for that reason. Call reset() after a
    reconnect or a vehicle reboot, when the sender starts counting again.
    """

    def __init__(self, window_s: float = 10.0) -> None:
        self.window_s = window_s
        self._last_seq: int | None = None
        self._last_time = 0.0
        self._stray_seq: int | None = None  # last out-of-sequence number, to spot a restart
        # (arrival time, packets missing just before this one)
        self._samples: deque[tuple[float, int]] = deque()

    def add(self, seq: int, now: float) -> None:
        lost = 0
        if self._last_seq is not None:
            gap = (seq - self._last_seq - 1) % SEQ_MODULO
            if gap < RESYNC_GAP or now - self._last_time >= OUTAGE_SILENCE_S:
                lost = gap
            elif self._stray_seq is None or seq != (self._stray_seq + 1) % SEQ_MODULO:
                # A duplicate or a late packet: received, but not part of the sequence.
                self._stray_seq = seq
                self._record(now, lost=0)
                return
            # Otherwise it continues from the previous stray number: the sender restarted.
        self._last_seq = seq
        self._stray_seq = None
        self._record(now, lost)

    def reset(self) -> None:
        """Start over, e.g. after a reconnect or a vehicle reboot."""
        self._last_seq = None
        self._stray_seq = None
        self._samples.clear()

    def loss_percent(self, now: float) -> float | None:
        """Share of packets lost inside the window; None if nothing arrived in it."""
        self._trim(now)
        received = len(self._samples)
        if received == 0:
            return None
        lost = sum(lost for _, lost in self._samples)
        return 100.0 * lost / (received + lost)

    def _record(self, now: float, lost: int) -> None:
        self._last_time = now
        self._samples.append((now, lost))
        self._trim(now)

    def _trim(self, now: float) -> None:
        horizon = now - self.window_s
        while self._samples and self._samples[0][0] <= horizon:
            self._samples.popleft()


class RateMeter:
    """Arrival rate of each message type: arrivals in the last window_s seconds / window_s.

    For a message type seen for less than a window, the time since its first
    arrival is used instead of window_s.
    """

    def __init__(self, window_s: float = 5.0) -> None:
        self.window_s = window_s
        self._arrivals: dict[str, deque[float]] = {}
        self._first_seen: dict[str, float] = {}

    def add(self, name: str, now: float) -> None:
        if name not in self._arrivals:
            self._arrivals[name] = deque()
            self._first_seen[name] = now
        arrivals = self._arrivals[name]
        arrivals.append(now)
        self._trim(arrivals, now)

    def names(self) -> list[str]:
        """Every message type seen so far."""
        return list(self._arrivals)

    def rate_hz(self, name: str, now: float) -> float | None:
        """Messages per second; 0.0 for a type never seen, None if seen too briefly to tell."""
        arrivals = self._arrivals.get(name)
        if arrivals is None:
            return 0.0
        start = max(now - self.window_s, self._first_seen[name])
        if now - start < MIN_OBSERVATION_S:
            return None
        self._trim(arrivals, now)
        return sum(1 for t in arrivals if t > start) / (now - start)

    def _trim(self, arrivals: deque[float], now: float) -> None:
        horizon = now - self.window_s
        while arrivals and arrivals[0] <= horizon:
            arrivals.popleft()
