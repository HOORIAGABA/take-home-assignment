from leadgen.dedupe import merge_duplicates
from leadgen.enrich.socials import extract_socials, is_social_url
from leadgen.maps.geo import City, Tile, initial_tiles
from leadgen.pipeline import category_matcher
from leadgen.util import normalize_phone


def test_socials():
    links = [
        "https://www.instagram.com/smile.dental/", "https://instagram.com/p/abc123",
        "https://www.facebook.com/SmileDentalLahore", "https://www.facebook.com/sharer/sharer.php?u=x",
        "https://facebook.com/profile.php?id=1000123", "https://pk.linkedin.com/company/smile-dental",
        "https://wa.me/923001234567", "https://api.whatsapp.com/send?phone=923211234567&text=hi",
    ]
    s = extract_socials(links, "Call or WhatsApp: 0300-7654321")
    assert s["instagram"] == ["https://www.instagram.com/smile.dental"]
    assert s["facebook"] == ["https://www.facebook.com/SmileDentalLahore", "https://www.facebook.com/profile.php?id=1000123"]
    assert s["linkedin"] == ["https://www.linkedin.com/company/smile-dental"]
    assert s["whatsapp"] == ["+923001234567", "+923211234567", "+923007654321"]
    assert is_social_url("https://m.facebook.com/x") and not is_social_url("https://smile.pk")


def test_phone_normalisation():
    assert normalize_phone("0300 1234567") == "+923001234567"
    assert normalize_phone("+92 42 35862588") == "+924235862588"
    assert normalize_phone("0092-300-1234567") == "+923001234567"
    assert normalize_phone("12") is None


def place(cid, name, lat, lng, phone=None, website=None, reviews=0):
    return {"cid": cid, "name": name, "lat": lat, "lng": lng, "phone": phone, "website": website,
            "review_count": reviews}


def test_dedupe_merges_duplicate_pins_but_keeps_branches():
    places = [
        place("1", "Smile Dental", 31.5000, 74.3000, "+92 300 1234567", reviews=50),
        place("2", "Smile Dental Clinic & Implant Centre", 31.5003, 74.3002, "0300 1234567", "http://smile.pk"),
        # same chain, same hotline, other side of town -> a branch, not a duplicate
        place("3", "Smile Dental - DHA", 31.4700, 74.4100, "0300 1234567", reviews=10),
        place("4", "Other Clinic", 31.5001, 74.3001, "0321 0000000"),
    ]
    kept, log = merge_duplicates(places)
    assert {p["cid"] for p in kept} == {"1", "3", "4"}
    winner = next(p for p in kept if p["cid"] == "1")
    assert winner["merged_cids"] == ["2"] and winner["website"] == "http://smile.pk"
    assert log[0]["kept"] == "1"


def test_tiles_split_and_contain():
    t = Tile(31.0, 74.0, 31.2, 74.2)
    kids = t.split()
    assert len(kids) == 4 and all(k.depth == 1 for k in kids)
    assert sum(k.contains(31.05, 74.15) for k in kids) == 1  # half-open: each point in exactly one child
    assert Tile.from_id(t.id, 0) == t
    assert kids[0].span_param() < t.span_param()


def test_initial_tiles_respect_city_polygon():
    square = [[[74.0, 31.0], [74.1, 31.0], [74.1, 31.1], [74.0, 31.1], [74.0, 31.0]]]
    city = City("X", 31.0, 31.4, 74.0, 74.4, [square])
    tiles = initial_tiles(city, 5)
    assert tiles and all(t.south < 31.15 and t.west < 74.15 for t in tiles)
    assert city.contains(31.05, 74.05) and not city.contains(31.3, 74.3)


def test_category_matcher():
    m = category_matcher("dental clinic")
    assert m.search("Dental clinic") and m.search("Dentist") and m.search("Denture care center")
    assert not m.search("Medical clinic") and not m.search("Accident lawyer")


def test_glued_numbers_rejected():
    assert normalize_phone("+92300097997950") is None          # 14 digits: two numbers run together
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"  # other countries: length not fixed
