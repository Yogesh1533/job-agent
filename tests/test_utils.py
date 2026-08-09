from job_agent.utils import strip_html


def test_strips_simple_tags():
    assert strip_html("<p>Hello</p>") == "Hello"


def test_strips_tags_with_attributes():
    assert strip_html('<a href="x">link</a>') == "link"


def test_collapses_tags_to_spaces():
    # Each tag becomes a space; surrounding whitespace is trimmed.
    assert strip_html("<b>a</b><i>b</i>") == "a  b"


def test_none_returns_empty_string():
    assert strip_html(None) == ""


def test_empty_string():
    assert strip_html("") == ""


def test_plain_text_unchanged():
    assert strip_html("no tags here") == "no tags here"


def test_html_entities_are_not_decoded():
    # Documents current behavior: entities pass through untouched.
    assert strip_html("<p>a &amp; b</p>") == "a &amp; b"
