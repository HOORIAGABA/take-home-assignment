from leadgen.enrich.emails import decode_cfemail, extract_emails, is_valid_email, valid_emails


def cf_encode(email: str, key: int = 0x5A) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def test_cloudflare_attr_and_href():
    hx = cf_encode("info@smile.pk")
    assert decode_cfemail(hx) == "info@smile.pk"
    html = f'<a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="{hx}">[email&#160;protected]</a>'
    assert valid_emails(html) == ["info@smile.pk"]
    html2 = f'<a href="/cdn-cgi/l/email-protection#{cf_encode("dr@clinic.com.pk")}">mail</a>'
    assert "dr@clinic.com.pk" in valid_emails(html2)


def test_written_out_forms():
    for text in ["info [at] site [dot] pk", "info(at)site(dot)pk", "info {at} site {dot} pk",
                 "Email: info at site dot pk", "info [at] site.pk"]:
        assert valid_emails(f"<p>{text}</p>") == ["info@site.pk"], text


def test_mailto_entities_and_js():
    assert valid_emails('<a href="mailto:Hello%40Dent.PK?subject=Hi">x</a>') == ["hello@dent.pk"]
    assert valid_emails("<p>care&#64;dent.pk</p>") == ["care@dent.pk"]
    js = "<script>var e = 'book' + '@' + 'teeth.com.pk'; location='mai'+'lto:'+e;</script>"
    assert "book@teeth.com.pk" in valid_emails(js)
    assert "a@b.pk" in valid_emails("<script>x='a\\u0040b.pk'</script>")
    codes = ",".join(str(ord(c)) for c in "fc@x.org")
    assert "fc@x.org" in valid_emails(f"<script>String.fromCharCode({codes})</script>")


def test_junk_is_filtered():
    html = """logo@2x.png you@example.com 5f2b1c9d8e7a6b5c4d3e2f1a0b9c8d7e@sentry.io
              name@domain.com a..b@x.pk real.person@clinic.pk"""
    assert valid_emails(html) == ["real.person@clinic.pk"]
    assert not is_valid_email("bundle@1.2.js")


def test_dedupes_and_lowercases():
    assert extract_emails("A@B.PK a@b.pk <a href='mailto:a@b.pk'>") == ["a@b.pk"]
