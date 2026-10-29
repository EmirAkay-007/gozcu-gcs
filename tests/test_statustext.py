from gcs.mav import mavlink
from gcs.statustext import INCOMPLETE_AFTER_S, INCOMPLETE_MARK, TEXT_LEN, StatusTextAssembler

WARNING = mavlink.MAV_SEVERITY_WARNING


def statustext(raw, msg_id=0, seq=0, severity=WARNING):
    """A STATUSTEXT as the parser delivers it: the text zero-padded to 50 bytes."""
    return mavlink.MAVLink_statustext_message(severity, raw.ljust(TEXT_LEN, b"\0"), msg_id, seq)


def split_like_ardupilot(text, msg_id=7):
    """Chunks as GCS::send_textv makes them: an empty last chunk after an exact fit."""
    raw = text.encode("utf-8")
    if len(raw) <= TEXT_LEN:
        return [statustext(raw)]
    chunks = []
    for seq in range(256):
        piece, raw = raw[:TEXT_LEN], raw[TEXT_LEN:]
        chunks.append(statustext(piece, msg_id, seq))
        if len(piece) < TEXT_LEN:
            return chunks
    raise AssertionError("text too long for the test")


def feed(assembler, messages, now=0.0):
    return [assembler.add(msg, now) for msg in messages]


def test_a_short_text_is_complete_at_once():
    assert feed(StatusTextAssembler(), split_like_ardupilot("Arming motors")) == [
        (WARNING, "Arming motors")
    ]


def test_non_ascii_letters_survive():
    (msg,) = split_like_ardupilot("Güç düşük: iniş")
    assert "�" in msg.text  # what pymavlink's own ASCII decoding makes of it
    assert StatusTextAssembler().add(msg, 0.0) == (WARNING, "Güç düşük: iniş")


def test_a_long_text_is_joined_from_its_chunks():
    text = "PreArm: Battery 1 below minimum arming voltage; check BATT_ARM_VOLT"
    results = feed(StatusTextAssembler(), split_like_ardupilot(text))
    assert results == [None, (WARNING, text)]


def test_a_text_filling_whole_chunks_ends_with_an_empty_one():
    text = "x" * (2 * TEXT_LEN)
    results = feed(StatusTextAssembler(), split_like_ardupilot(text))
    assert results == [None, None, (WARNING, text)]


def test_a_letter_split_between_two_chunks_stays_whole():
    text = "a" * (TEXT_LEN - 1) + "ü" + "b" * 10  # "ü" is two bytes: one in each chunk
    assert feed(StatusTextAssembler(), split_like_ardupilot(text))[-1] == (WARNING, text)


def test_chunks_may_arrive_out_of_order():
    text = "c" * 60
    results = feed(StatusTextAssembler(), reversed(split_like_ardupilot(text)))
    assert results == [None, (WARNING, text)]


def test_texts_with_different_ids_do_not_mix():
    first = split_like_ardupilot("A" * 60, msg_id=1)
    second = split_like_ardupilot("B" * 60, msg_id=2)
    results = feed(StatusTextAssembler(), [first[0], second[0], second[1], first[1]])
    assert results == [None, None, (WARNING, "B" * 60), (WARNING, "A" * 60)]


def test_a_text_missing_a_chunk_is_shown_incomplete_after_a_while():
    assembler = StatusTextAssembler()
    first_chunk = split_like_ardupilot("d" * 70)[0]
    assert assembler.add(first_chunk, now=0.0) is None
    assert assembler.expire(now=INCOMPLETE_AFTER_S / 2) == []
    assert assembler.expire(now=INCOMPLETE_AFTER_S) == [(WARNING, "d" * 50 + INCOMPLETE_MARK)]
    assert assembler.expire(now=10.0) == []  # reported once
