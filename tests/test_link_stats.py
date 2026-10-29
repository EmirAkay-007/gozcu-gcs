import pytest

from gcs.link_stats import PacketLossMeter, RateMeter


def feed(meter, seqs, step=0.1):
    """Feed sequence numbers that arrive back to back, `step` seconds apart."""
    for i, seq in enumerate(seqs):
        meter.add(seq, now=i * step)


def lost_and_received(loss_percent, received):
    """Turn a loss percentage back into a lost-packet count."""
    return round(loss_percent * received / (100 - loss_percent))


def test_no_loss_across_the_255_to_0_wraparound():
    meter = PacketLossMeter()
    feed(meter, [253, 254, 255, 0, 1, 2])
    assert meter.loss_percent(now=0.5) == 0.0


def test_a_gap_counts_the_missing_packets():
    meter = PacketLossMeter()
    feed(meter, [10, 11, 14])  # 12 and 13 never arrived
    assert meter.loss_percent(now=0.2) == pytest.approx(100 * 2 / 5)


def test_a_duplicate_is_not_loss():
    meter = PacketLossMeter()
    feed(meter, [10, 11, 11, 12])
    assert meter.loss_percent(now=0.3) == 0.0


def test_a_late_packet_costs_at_most_one():
    meter = PacketLossMeter()
    feed(meter, [10, 11, 13, 12, 14, 15])  # 12 overtaken by 13
    assert lost_and_received(meter.loss_percent(now=0.5), received=6) == 1


def test_an_outage_after_silence_is_loss_even_past_half_the_range():
    meter = PacketLossMeter()
    feed(meter, range(10))  # 0..9 until t = 0.9 s
    meter.add(160, now=5.0)  # 150 packets missing during 4 s of silence
    assert lost_and_received(meter.loss_percent(now=5.0), received=11) == 150


def test_a_restarted_sender_is_followed_without_phantom_loss():
    meter = PacketLossMeter()
    feed(meter, [200, 201, 100, 101, 102])  # counter restarted at 100
    assert meter.loss_percent(now=0.4) == 0.0


def test_old_losses_leave_the_window():
    meter = PacketLossMeter(window_s=1.0)
    meter.add(0, now=0.0)
    meter.add(5, now=0.1)  # 4 lost
    meter.add(6, now=5.0)
    meter.add(7, now=5.1)
    assert meter.loss_percent(now=5.1) == 0.0


def test_reset_forgets_the_previous_sequence():
    meter = PacketLossMeter()
    meter.add(0, now=0.0)
    meter.reset()
    meter.add(100, now=0.1)  # a 99-packet gap without the reset
    assert meter.loss_percent(now=0.1) == 0.0


def test_loss_is_unknown_before_any_packet():
    assert PacketLossMeter().loss_percent(now=0.0) is None


def test_rate_of_a_steady_stream():
    meter = RateMeter(window_s=5.0)
    for i in range(51):
        meter.add("ATTITUDE", now=i / 10)  # 10 Hz for 5 s
    assert meter.rate_hz("ATTITUDE", now=5.0) == pytest.approx(10.0)


def test_rate_during_the_first_window():
    meter = RateMeter(window_s=5.0)
    for i in range(11):
        meter.add("ATTITUDE", now=i / 10)  # 10 Hz for 1 s only
    assert meter.rate_hz("ATTITUDE", now=1.0) == pytest.approx(10.0)


def test_rate_falls_while_a_stream_is_silent():
    meter = RateMeter(window_s=5.0)
    for i in range(51):
        meter.add("VFR_HUD", now=i / 10)  # 10 Hz until t = 5 s, then nothing
    assert meter.rate_hz("VFR_HUD", now=7.5) == pytest.approx(5.0)
    assert meter.rate_hz("VFR_HUD", now=10.0) == 0.0


def test_rate_of_a_slow_stream():
    meter = RateMeter(window_s=15.0)
    for t in range(0, 31, 5):
        meter.add("HOME_POSITION", now=float(t))  # 0.2 Hz
    assert meter.rate_hz("HOME_POSITION", now=30.0) == pytest.approx(0.2)


def test_rate_is_unknown_right_after_the_first_arrival():
    meter = RateMeter()
    meter.add("HOME_POSITION", now=0.0)
    meter.add("HOME_POSITION", now=0.001)  # a burst must not read as 1000 Hz
    assert meter.rate_hz("HOME_POSITION", now=0.001) is None


def test_unseen_message_has_zero_rate():
    assert RateMeter().rate_hz("SYS_STATUS", now=0.0) == 0.0
