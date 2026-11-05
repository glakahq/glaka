"""Split a document into chunks small enough to embed and to quote."""

import re

MAX_CHARS = 800


def split_into_chunks(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split on blank lines (paragraphs), then pack paragraphs into chunks.

    Markdown headings start a new chunk, so a chunk never mixes two sections.
    A single paragraph longer than max_chars becomes its own chunk.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        starts_section = paragraph.startswith("#")
        fits = len(current) + len(paragraph) + 2 <= max_chars
        if current and (starts_section or not fits):
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current)
    return chunks
