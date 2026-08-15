"""Turn a natural-language question into match constraints.

Vector search always returns *something* from a populated graph. Without this
filter, a question about an unknown person still expands the neighbourhood of
whatever Mumbai chunk happened to sit nearest in embedding space — which looks
like the UI is stuck on the previous answer.
"""

from __future__ import annotations

import re

from app.services import lexicon

# Function words and question scaffolding. Domain terms (drugs, cities, names)
# are kept; they come from the user's question, not from a case list.
_STOP = {
    "what", "happened", "happen", "to", "on", "and", "the", "a", "an", "in", "of",
    "how", "many", "much", "were", "was", "is", "are", "am", "be", "been", "being",
    "his", "her", "their", "its", "who", "which", "for", "with", "from", "about",
    "this", "that", "these", "those", "into", "over", "after", "before", "between",
    "please", "tell", "give", "show", "list", "any", "all", "some", "did", "does",
    "have", "has", "had", "than", "then", "also", "just", "not", "but", "or", "if",
    "when", "where", "why", "whom", "whose", "there", "here", "out", "up", "down",
    "alleged", "role", "people", "person", "involved", "involvement", "syndicate",
    "network", "question", "information", "details", "report", "regarding",
    "case", "cases", "crime", "crimes", "police", "accused", "arrest", "arrested",
    "seized", "seizure", "seizures", "operation", "registered", "connected",
    "across", "using", "based", "named", "known", "called", "according",
    "officials", "officer", "officers", "authorities", "agency", "drug", "drugs",
    "narcotics", "trafficking", "trafficker", "supply", "ring", "bust", "raid",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}

_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b")
_QUOTED = re.compile(r'"([^"]{2,80})"')
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'.-]{2,}")


class QueryFocus:
    __slots__ = ("phrases", "tokens", "raw")

    def __init__(self, raw: str, phrases: list[str], tokens: list[str]) -> None:
        self.raw = raw
        self.phrases = phrases
        self.tokens = tokens

    @property
    def constrained(self) -> bool:
        return bool(self.phrases or self.tokens)

    def lucene_entity_query(self) -> str:
        """Phrase query for the fulltext entity index, or empty if unconstrained."""
        if self.phrases:
            return " OR ".join(f'"{p}"' for p in self.phrases)
        if self.tokens:
            return " AND ".join(self.tokens[:6])
        return ""

    def to_dict(self) -> dict:
        return {"phrases": self.phrases, "tokens": self.tokens}


def extract_focus(query: str) -> QueryFocus:
    text = (query or "").strip()
    phrases: list[str] = []
    for match in _QUOTED.findall(text):
        phrases.append(lexicon.normalize(match))
    for match in _NAME.findall(text):
        phrases.append(lexicon.normalize(match))

    # Dedup while keeping order.
    seen: set[str] = set()
    unique_phrases = []
    for phrase in phrases:
        if phrase and phrase not in seen and phrase not in _STOP:
            seen.add(phrase)
            unique_phrases.append(phrase)

    tokens: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        folded = lexicon.normalize(token)
        if not folded or folded in _STOP or folded in seen:
            continue
        if folded in {p for p in unique_phrases}:
            continue
        # Skip tokens already inside a captured name.
        if any(folded in phrase.split() for phrase in unique_phrases):
            continue
        tokens.append(folded)

    # Drug / agency terms from the lexicon always count, even if they were in STOP.
    for drug, _ in lexicon.find_drugs(text):
        if drug not in tokens and drug not in unique_phrases:
            tokens.append(drug)
    for agency in lexicon.find_agencies(text):
        if agency not in tokens:
            tokens.append(agency)

    return QueryFocus(raw=text, phrases=unique_phrases, tokens=tokens[:12])


def blob_matches(title: str, body: str, focus: QueryFocus) -> bool:
    if not focus.constrained:
        return True
    blob = lexicon.normalize(f"{title or ''} {body or ''}")
    if not blob:
        return False
    if focus.phrases:
        # Union of named people/places in the question. Requiring every phrase
        # would drop a case that names only one of two people the analyst asked about.
        return any(phrase in blob for phrase in focus.phrases)
    tokens = [t for t in focus.tokens if len(t) >= 4]
    if not tokens:
        return True
    hits = sum(1 for token in tokens if token in blob)
    needed = 1 if len(tokens) == 1 else min(2, len(tokens))
    return hits >= needed
