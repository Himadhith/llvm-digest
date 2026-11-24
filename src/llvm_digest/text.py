"""Text helpers: HTML/Markdown to plain text, sentence extraction, Slack-safe escaping."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "svg", "aside", "noscript"}  # aside = quoted replies in Discourse
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "hr", "details"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")
        elif tag == "li":  # pragma: no cover - li is in _BLOCK_TAGS
            self._parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    """Convert Discourse ``cooked`` HTML to plain text (quotes and scripts dropped)."""
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # malformed HTML must never break a run  # noqa: BLE001
        return _collapse(re.sub(r"<[^>]+>", " ", html or ""))
    return _collapse(parser.text())


_FENCE = re.compile(r"```.*?```", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_URL = re.compile(r"https?://\S+")


def markdown_to_text(md: str) -> str:
    """Strip Markdown noise (code fences, images, links, comments) from a GitHub issue body."""
    text = md or ""
    text = _COMMENT.sub(" ", text)
    text = _FENCE.sub("\n", text)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _HTML_TAG.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _URL.sub(" ", text)
    return _collapse(text)


def _collapse(text: str) -> str:
    text = text.replace("\r", "")
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[\(`\"'0-9])")
_TERMINAL = (".", "!", "?", ":")


def prose(text: str) -> str:
    """Keep only sentence-like lines: drops bare headings ("Description") and dangling lead-ins
    ("The following code:") left behind once code blocks are removed."""
    kept: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        words = len(line.split())
        if not line:
            continue
        if words <= 3 and not line.endswith(_TERMINAL):
            continue  # heading-like
        if line.endswith(":") and words < 8:
            continue  # lead-in to a removed code block
        kept.append(line)
    return " ".join(kept)


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` chars on a word boundary, adding an ellipsis."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: max(0, limit - 1)]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "\u2026"


def first_sentences(text: str, count: int = 2, limit: int = 320) -> str:
    """Return the first ``count`` sentences of ``text``, capped at ``limit`` characters."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    sentences = _SENTENCE_END.split(flat)
    return truncate(" ".join(sentences[:count]), limit)


def sentences(text: str, limit: int = 6) -> list[str]:
    """Split text into up to ``limit`` sentences."""
    flat = " ".join(text.split())
    return [s for s in _SENTENCE_END.split(flat) if s][:limit]


_MENTION = re.compile(r"@(channel|here|everyone)\b", re.I)


def slack_escape(text: str) -> str:
    """Escape untrusted text for Slack mrkdwn: HTML entities, and neutralise broadcast mentions."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MENTION.sub(lambda m: "@\u200b" + m.group(1), escaped)


def slack_label(text: str) -> str:
    """Escape text used inside a ``<url|label>`` link (pipes and angle brackets would break it)."""
    return slack_escape(text).replace("|", "\u00a6")
