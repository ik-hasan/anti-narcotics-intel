"""Sentence-aware chunking with no tokenizer dependency.

Token counts are approximated at 4 characters per token. Loading tiktoken or a HF
tokenizer would cost more RAM than the accuracy is worth on a 512 MB instance, and
Voyage bills by its own count regardless.
"""

import hashlib
import re

from app.config import settings

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u0900-\u097F\"'(])")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")

CHARS_PER_TOKEN = 4


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(clean_text(text).encode("utf-8")).hexdigest()[:32]


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences.extend(s.strip() for s in _SENTENCE_SPLIT.split(paragraph) if s.strip())
    return sentences


def chunk_text(
    text: str,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[str]:
    target = (target_tokens or settings.chunk_tokens) * CHARS_PER_TOKEN
    overlap = (overlap_tokens or settings.chunk_overlap_tokens) * CHARS_PER_TOKEN

    cleaned = clean_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= target:
        return [cleaned]

    sentences = _split_sentences(cleaned)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        # A single sentence longer than the window gets hard-split on whitespace.
        if len(sentence) > target:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            for i in range(0, len(sentence), target):
                chunks.append(sentence[i : i + target])
            continue

        if current_len + len(sentence) + 1 > target and current:
            chunks.append(" ".join(current))
            carry: list[str] = []
            carry_len = 0
            for prev in reversed(current):
                if carry_len + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                carry_len += len(prev) + 1
            current, current_len = carry, carry_len

        current.append(sentence)
        current_len += len(sentence) + 1

    if current:
        chunks.append(" ".join(current))

    return [c for c in (c.strip() for c in chunks) if len(c) >= 40]


def build_chunks(article_url: str, text: str) -> list[dict[str, object]]:
    return [
        {
            "id": f"{content_hash(article_url)}:{position}",
            "text": chunk,
            "position": position,
            "article_url": article_url,
            "token_estimate": len(chunk) // CHARS_PER_TOKEN,
        }
        for position, chunk in enumerate(chunk_text(text))
    ]
