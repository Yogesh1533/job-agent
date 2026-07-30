import re

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    return _TAG_RE.sub(" ", text or "").strip()
