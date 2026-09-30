"""Tests for GET /api/updates/rss — RSS 2.0 XML feed endpoint."""
import xml.etree.ElementTree as ET


def test_rss_feed_returns_200_and_xml(client):
    """RSS endpoint must return HTTP 200 and application/xml header."""
    r = client.get("/api/updates/rss")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")


def test_rss_feed_valid_xml_structure(client):
    """RSS output must parse as valid XML containing channel metadata and items."""
    r = client.get("/api/updates/rss")
    root = ET.fromstring(r.text)
    assert root.tag == "rss"
    assert root.attrib.get("version") == "2.0"

    channel = root.find("channel")
    assert channel is not None
    assert channel.find("title").text == "JanDrishti — Live PIB Feed"

    items = channel.findall("item")
    assert len(items) > 0, "Expected at least one <item> element in the RSS feed"

    first_item = items[0]
    assert first_item.find("title") is not None
    assert first_item.find("link") is not None
    assert first_item.find("guid") is not None
