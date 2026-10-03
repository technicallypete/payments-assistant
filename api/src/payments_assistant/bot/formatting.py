"""Model Markdown → Telegram HTML (parse_mode=HTML), conservatively.

Telegram's HTML mode supports a small tag set (<b>, <i>, <code>, <a href>), and rejects the whole
message on malformed markup, so this converts only unambiguous patterns and escapes everything
else. Amounts ("$1,200.00"), ids ("in_123_abc") and URLs are never touched by emphasis rules.
"""

import html
import re

TELEGRAM_LIMIT = 4096

_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"https?://[^\s<]+")
_BOLD_STARS = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDERSCORES = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDERSCORE = re.compile(r"(?<!\w)_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?!\w)")
_LIST_ITEM = re.compile(r"^(\s*)[-*] (?=\S)", re.MULTILINE)


def to_telegram_html(markdown: str) -> str:
    text = html.escape(markdown, quote=False)
    protected: list[str] = []

    def protect(fragment: str) -> str:
        protected.append(fragment)
        return f"\x00{len(protected) - 1}\x00"

    # Code and links first, so their contents are never reinterpreted as emphasis.
    text = _CODE.sub(lambda m: protect(f"<code>{m.group(1)}</code>"), text)
    text = _LINK.sub(
        lambda m: protect(f'<a href="{m.group(2).replace(chr(34), "&quot;")}">{m.group(1)}</a>'),
        text,
    )
    text = _URL.sub(lambda m: protect(m.group(0)), text)
    text = _LIST_ITEM.sub(lambda m: f"{m.group(1)}• ", text)
    text = _BOLD_STARS.sub(r"<b>\1</b>", text)
    text = _BOLD_UNDERSCORES.sub(r"<b>\1</b>", text)
    text = _ITALIC_STAR.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDERSCORE.sub(r"<i>\1</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on paragraph, then line, boundaries; hard-split only lines longer than `limit`."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.rstrip("\n"))
        current = ""

    for paragraph in text.split("\n\n"):
        piece = paragraph if not current else "\n\n" + paragraph
        if len(current) + len(piece) <= limit:
            current += piece
            continue
        flush()
        for line in paragraph.split("\n"):
            piece = line if not current else "\n" + line
            if len(current) + len(piece) <= limit:
                current += piece
                continue
            flush()
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    flush()
    return chunks
