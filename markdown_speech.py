"""Strip markdown formatting so TTS reads natural speech."""

from __future__ import annotations

import html
import re

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_AUTOLINK_RE = re.compile(r"<(https?://[^>]+)>")
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)")
_STRIKE_RE = re.compile(r"~~([^~]+)~~")
_UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_ORDERED_LIST_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\|[-:\s|]+\|\s*$", re.MULTILINE)
_SPECIAL_CHARS_RE = re.compile(r"[*_~`#>|\\]")


def strip_markdown_for_speech(text: str) -> str:
    """Convert markdown-ish assistant text into plain speech-friendly text."""
    if not text:
        return ""

    cleaned = text.replace("\r\n", "\n")
    cleaned = _CODE_BLOCK_RE.sub(" ", cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _IMAGE_RE.sub(r"\1", cleaned)
    cleaned = _LINK_RE.sub(r"\1", cleaned)
    cleaned = _AUTOLINK_RE.sub(r"\1", cleaned)
    cleaned = _HEADER_RE.sub("", cleaned)
    cleaned = _BLOCKQUOTE_RE.sub("", cleaned)
    cleaned = _TABLE_SEP_RE.sub(" ", cleaned)
    cleaned = _TABLE_ROW_RE.sub(lambda m: m.group(1).replace("|", ", "), cleaned)
    cleaned = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", cleaned)
    cleaned = _STRIKE_RE.sub(r"\1", cleaned)
    cleaned = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", cleaned)
    cleaned = _UNORDERED_LIST_RE.sub("", cleaned)
    cleaned = _ORDERED_LIST_RE.sub("", cleaned)
    cleaned = _HR_RE.sub(" ", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _SPECIAL_CHARS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
