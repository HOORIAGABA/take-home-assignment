from pathlib import Path

import pytest

from leadgen.maps.parse import Blocked, SchemaChanged, ftid_to_cid, parse_search_payload

FIX = Path(__file__).parent / "fixtures" / "maps_search_page.txt"


def test_parses_real_payload():
    places = parse_search_payload(FIX.read_text("utf-8"))
    assert len(places) == 20
    assert len({p.cid for p in places}) == 20
    p = places[0]
    assert p.name and p.cid.isdigit() and p.place_id.startswith("ChIJ")
    assert 31 < p.lat < 32 and 74 < p.lng < 75
    assert p.category == "Dental clinic"
    assert sum(1 for x in places if x.phone) >= 15
    assert any(x.website for x in places)
    assert all(x.rating is None or 1 <= x.rating <= 5 for x in places)


def test_cid_from_ftid():
    assert ftid_to_cid("0x391905ba6e16d591:0x41db34e599450dc8") == str(0x41DB34E599450DC8)
    assert ftid_to_cid("garbage") is None


def test_captcha_page_is_blocked_not_empty():
    with pytest.raises(Blocked):
        parse_search_payload("<html>Our systems have detected unusual traffic</html>")


def test_changed_format_raises_instead_of_returning_zero():
    with pytest.raises(SchemaChanged):
        parse_search_payload(")]}'\n{\"new\": \"format\"}")
    with pytest.raises(SchemaChanged):
        parse_search_payload(")]}'\n" + "[" + ",".join(["[1,[2,3]]"] * 12) + "]")


def test_genuine_no_results_page_is_empty():
    payload = ")]}'\n" + '[["dental clinic",null]' + ",null" * 70 + "]"
    assert parse_search_payload(payload) == []
