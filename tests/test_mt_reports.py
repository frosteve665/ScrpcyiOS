import struct
import pytest
import mt_reports as m

CONTACT, RELEASE = 0xC2, 0x02
TS = 0x0102030405


def test_single_report_matches_proven_layout():
    r = m.build_single(CONTACT, 1000, 2000, TS)
    assert len(r) == 58
    assert r[0] == 0x09 and r[1:3] == b"\x01\x05" and r[3] == CONTACT
    assert struct.unpack("<HH", r[4:8]) == (1000, 2000)
    assert r[8:40] == b"\x00" * 32
    assert r[40:44] == b"\x02\x00\x00\x00"
    assert int.from_bytes(r[44:50], "little") == TS
    assert r[50:] == b"\x00" * 8


@pytest.mark.parametrize("variant", m.VARIANTS)
def test_two_contact_keeps_first_contact_and_tail_intact(variant):
    one = m.build_single(CONTACT, 1000, 2000, TS)
    two = m.build_two_contact(variant, (CONTACT, 1000, 2000), (CONTACT, 30000, 40000), TS)
    assert len(two) == 58
    assert two[:8] == one[:8]          # first contact untouched
    assert two[40:] == one[40:]        # constant + timestamp untouched


@pytest.mark.parametrize("variant", m.VARIANTS)
def test_two_contact_writes_second_position_in_reserved_area(variant):
    two = m.build_two_contact(variant, (CONTACT, 1, 2), (RELEASE, 30000, 40000), TS)
    area = two[8:40]
    assert struct.pack("<HH", 30000, 40000) in area
    assert bytes([RELEASE]) in area


def test_variants_are_distinct():
    outs = {v: m.build_two_contact(v, (CONTACT, 1, 2), (CONTACT, 3000, 4000), TS) for v in m.VARIANTS}
    assert len(set(outs.values())) == len(m.VARIANTS) >= 3


def test_unknown_variant_rejected():
    with pytest.raises(ValueError):
        m.build_two_contact("nope", (CONTACT, 1, 2), (CONTACT, 3, 4), TS)


def test_single_report_is_byte_identical_to_pymobiledevice3():
    hid = pytest.importorskip("pymobiledevice3.remote.core_device.hid_service")
    for state in (CONTACT, RELEASE):
        assert m.build_single(state, 12345, 54321, TS) == hid.build_touchscreen_report(state, 12345, 54321, TS)
