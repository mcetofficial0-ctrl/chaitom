"""Render a small Markdown subset as safe, independently valid Telegram HTML."""

import html
import re

from free_chat import split_telegram_text


INLINE = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*")
FENCE = re.compile(r"^```[^\n]*\n(.*?)(?:^```[^\n]*(?:\n|$)|\Z)", re.M | re.S)


def _inline(text):
    start = 0
    for match in INLINE.finditer(text):
        yield text[start:match.start()], ""
        yield (match.group(1), "code") if match.group(1) is not None else (match.group(2), "b")
        start = match.end()
    yield text[start:], ""


def _paragraphs(text):
    for line in text.splitlines(keepends=True):
        heading = re.match(r"^#{1,6}\s+(.+?)(\n?)$", line)
        if heading:
            yield heading.group(1).strip().strip("*"), "b"
            yield heading.group(2), ""
        else:
            line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
            yield from _inline(line)


def _segments(text):
    start = 0
    for match in FENCE.finditer(text):
        yield from _paragraphs(text[start:match.start()])
        yield match.group(1), "pre"
        yield "\n", ""
        start = match.end()
    yield from _paragraphs(text[start:])


def format_chat_reply(text, limit=4000):
    """Escape all model HTML; split visible text before wrapping allowed tags."""
    if limit < 2:
        raise ValueError("limit must be at least 2")
    chunks, parts, used = [], [], 0
    for content, tag in _segments(text):
        while content:
            remaining = limit - used
            first_size = 2 if ord(content[0]) > 0xFFFF else 1
            if remaining < first_size:
                chunks.append("".join(parts))
                parts, used = [], 0
                remaining = limit
            # The splitter requires room for at least one astral character.
            piece = content[:1] if remaining == 1 else split_telegram_text(content, remaining)[0]
            escaped = html.escape(piece, quote=False)
            parts.append(f"<{tag}>{escaped}</{tag}>" if tag else escaped)
            used += len(piece.encode("utf-16-le")) // 2
            content = content[len(piece):]
    if parts:
        chunks.append("".join(parts))
    # Telegram rejects messages containing only whitespace (e.g. a trailing
    # newline after an exactly full code block).
    return [chunk for chunk in chunks
            if html.unescape(re.sub(r"</?(?:b|code|pre)>", "", chunk)).strip()]
