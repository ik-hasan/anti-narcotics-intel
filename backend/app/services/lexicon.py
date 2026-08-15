"""Zero-dependency narcotics lexicon.

This runs before any Gemini call. Its job is to cheaply decide whether a document
is worth an LLM round trip and to score candidate URLs during OSINT discovery,
which is why it is pure regex and dictionaries with no model weights.
"""

import re
import unicodedata

DRUG_TERMS: dict[str, str] = {
    "heroin": "opioid",
    "smack": "opioid",
    "brown sugar": "opioid",
    "opium": "opioid",
    "poppy husk": "opioid",
    "doda": "opioid",
    "morphine": "opioid",
    "fentanyl": "opioid",
    "tramadol": "opioid",
    "cocaine": "stimulant",
    "charas": "cannabis",
    "hashish": "cannabis",
    "hash": "cannabis",
    "ganja": "cannabis",
    "cannabis": "cannabis",
    "marijuana": "cannabis",
    "bhang": "cannabis",
    "mdma": "stimulant",
    "ecstasy": "stimulant",
    "methamphetamine": "stimulant",
    "meth": "stimulant",
    "mephedrone": "stimulant",
    "md": "stimulant",
    "amphetamine": "stimulant",
    "ketamine": "dissociative",
    "lsd": "hallucinogen",
    "alprazolam": "pharma",
    "codeine": "pharma",
    "pseudoephedrine": "precursor",
    "ephedrine": "precursor",
    "acetic anhydride": "precursor",
}

AGENCY_TERMS: dict[str, str] = {
    "ncb": "agency",
    "narcotics control bureau": "agency",
    "dri": "agency",
    "directorate of revenue intelligence": "agency",
    "customs": "agency",
    "anti narcotics cell": "agency",
    "anti-narcotics cell": "agency",
    "anc": "agency",
    "ncb mumbai": "agency",
    "police": "agency",
    "crime branch": "agency",
    "bsf": "agency",
    "border security force": "agency",
    "coast guard": "agency",
    "sog": "agency",
    "cbi": "agency",
    "ed": "agency",
    "enforcement directorate": "agency",
}

ACTION_TERMS = {
    "raid",
    "raided",
    "seizure",
    "seized",
    "seizes",
    "bust",
    "busted",
    "arrest",
    "arrested",
    "smuggling",
    "smuggler",
    "trafficking",
    "trafficker",
    "peddler",
    "peddling",
    "consignment",
    "contraband",
    "narcotics",
    "narcotic",
    "ndps",
    "drug",
    "drugs",
    "haul",
    "intercepted",
    "recovered",
    "cartel",
    "syndicate",
    "racket",
    "module",
}

# NDPS Act is the Indian narcotics statute; a mention is a strong relevance signal.
STATUTE_PATTERN = re.compile(r"\bndps\b|narcotic\s+drugs\s+and\s+psychotropic", re.IGNORECASE)

QUANTITY_PATTERN = re.compile(
    r"(\d[\d,]*\.?\d*)\s*"
    r"(kg|kgs|kilogram|kilograms|gm|gms|gram|grams|g|mg|tonne|tonnes|ton|tons|"
    r"litre|litres|liter|liters|ml|tablet|tablets|pill|pills|capsule|capsules|strip|strips)\b",
    re.IGNORECASE,
)

VALUE_PATTERN = re.compile(
    r"(?:rs\.?|inr|₹)\s*(\d[\d,]*\.?\d*)\s*(crore|lakh|cr|lakhs|crores|million|billion)?",
    re.IGNORECASE,
)

UNIT_TO_GRAMS = {
    "mg": 0.001,
    "g": 1.0,
    "gm": 1.0,
    "gms": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kgs": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "ton": 1_000_000.0,
    "tons": 1_000_000.0,
    "tonne": 1_000_000.0,
    "tonnes": 1_000_000.0,
}

_HONORIFICS = {
    "mr", "mrs", "ms", "dr", "shri", "smt", "sri", "md", "sh",
    "inspector", "constable", "officer", "accused", "one", "the",
}

_WHITESPACE = re.compile(r"\s+")
_NON_NAME = re.compile(r"[^\w\s\-'.]", re.UNICODE)


def normalize(text: str) -> str:
    """Fold to a stable comparison form used for MERGE keys."""
    text = unicodedata.normalize("NFKC", text)
    text = _NON_NAME.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text


def canonical_person(name: str) -> str:
    """Strip honorifics so 'Shri Ramesh Kumar' and 'Ramesh Kumar' merge into one node."""
    tokens = [t for t in normalize(name).replace(".", " ").split() if t]
    while tokens and tokens[0] in _HONORIFICS:
        tokens.pop(0)
    return " ".join(tokens) or normalize(name)


def display_person(name: str) -> str:
    """Drop leading honorifics while keeping the source casing for display."""
    tokens = [t for t in name.strip().replace(".", " ").split() if t]
    while len(tokens) > 1 and tokens[0].lower().strip(".") in _HONORIFICS:
        tokens.pop(0)
    return " ".join(tokens) or name.strip()


def canonical_key(name: str) -> str:
    return normalize(name)


def find_drugs(text: str) -> list[tuple[str, str]]:
    lowered = f" {normalize(text)} "
    found: list[tuple[str, str]] = []
    for term, category in DRUG_TERMS.items():
        # Two-token terms like "brown sugar" need substring matching, not word sets.
        if re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", lowered):
            found.append((term, category))
    return found


def find_agencies(text: str) -> list[str]:
    lowered = f" {normalize(text)} "
    return [
        term
        for term in AGENCY_TERMS
        if re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", lowered)
    ]


def find_quantities(text: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for match in QUANTITY_PATTERN.finditer(text):
        raw_value, unit = match.group(1), match.group(2).lower()
        try:
            value = float(raw_value.replace(",", ""))
        except ValueError:
            continue
        results.append(
            {
                "quantity": value,
                "unit": unit,
                "grams": value * UNIT_TO_GRAMS[unit] if unit in UNIT_TO_GRAMS else None,
            }
        )
    return results


def relevance_score(text: str) -> float:
    """Cheap 0..1 narcotics-relevance estimate.

    Used two ways: to skip Gemini calls on irrelevant ingests, and to rank candidate
    URLs in the OSINT filter before we spend bandwidth fetching them.
    """
    if not text or not text.strip():
        return 0.0

    normalized = normalize(text)
    words = set(normalized.split())

    drug_hits = len(find_drugs(text))
    action_hits = len(words & ACTION_TERMS)
    agency_hits = len(find_agencies(text))
    statute_hit = 1 if STATUTE_PATTERN.search(text) else 0
    quantity_hit = 1 if QUANTITY_PATTERN.search(text) else 0

    score = (
        min(drug_hits, 3) * 0.15
        + min(action_hits, 5) * 0.08
        + min(agency_hits, 2) * 0.10
        + statute_hit * 0.15
        + quantity_hit * 0.10
    )
    return round(min(score, 1.0), 3)


def summarize_signals(text: str) -> dict[str, object]:
    """Structured lexicon output, attached to every ingested article for explainability."""
    drugs = find_drugs(text)
    return {
        "relevance": relevance_score(text),
        "drug_terms": [d[0] for d in drugs],
        "drug_categories": sorted({d[1] for d in drugs}),
        "agencies": find_agencies(text),
        "quantities": find_quantities(text)[:10],
        "mentions_ndps": bool(STATUTE_PATTERN.search(text)),
    }
