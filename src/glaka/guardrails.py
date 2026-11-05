"""Safety checks around the AI. Each returns human-readable warnings for the owner.

These are deterministic (regexes, no AI), so they are fast, free and testable.
Any warning makes a draft "not grounded", which means it is never auto-sent and
the owner sees why in the inbox.
"""

import re

# --- input: what the customer sent -------------------------------------------------

_INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules|prompts?|guidelines|above|previous)\b",
    r"\b(system|developer|hidden)\s+(prompt|message|instructions?)\b",
    r"\b(reveal|show|print|repeat)\b.{0,30}\b(prompt|instructions|rules)\b",
    r"\byou are now\b|\bact as\b|\bpretend (to be|you are)\b|\bnew instructions\b",
    r"\b(jailbreak|developer mode|DAN mode)\b",
    r"<\|im_start\|>|\[/?INST\]|<\|system\|>",
    # Fake role labels ("SYSTEM: ...") and policy announcements aimed at the AI.
    r"(?:^|\n)\s*(?:system|assistant|developer|admin)\s*:",
    r"\bnew (?:policy|rule)s?\b.{0,40}\b(?:prices?|discount|from (?:now|today))\b",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE | re.DOTALL)


def injection_warnings(message: str) -> list[str]:
    """Flag messages that try to give the AI instructions. The AI is also told to ignore
    such text; this makes sure the owner notices the attempt too."""
    match = _INJECTION.search(message)
    if match:
        return [f"possible prompt injection in the message: \"{match.group(0)[:60]}\""]
    return []


# --- output: what the AI wrote ------------------------------------------------------

_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# Not inside a price or decimal: no £/$/€ or digit-separator before, no ".5"/",000" after.
_PHONE = re.compile(r"(?<![\w£$€.,])\+?\d[\d\s().-]{8,}\d(?!\w|[.,]\d)")
_SOURCE_NUMBERS = re.compile(r"\[\d+\]|\bcited_sources\b|\bsource \d+\b", re.IGNORECASE)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def unsupported_contacts(answer: str, sources: list[str]) -> list[str]:
    """Links, email addresses and phone numbers in the reply that no source contains.

    A reply pointing customers at an unknown link or number is a classic way for an
    injected message to turn the assistant into a phishing tool.
    """
    source_text = "\n".join(sources).lower()
    source_digits = {_digits(m) for m in _PHONE.findall("\n".join(sources))}
    found = []
    for url in _URL.findall(answer):
        if url.rstrip(".,;:!?").lower() not in source_text:
            found.append(url.rstrip(".,;:!?"))
    for email in _EMAIL.findall(answer):
        if email.lower() not in source_text:
            found.append(email)
    for phone in _PHONE.findall(answer):
        if len(_digits(phone)) >= 10 and _digits(phone) not in source_digits:
            found.append(phone.strip())
    return found


def leaks_internals(answer: str) -> bool:
    """The customer reads the reply as-is, so it must not mention our source numbering."""
    return bool(_SOURCE_NUMBERS.search(answer))


# --- email: is this a person writing to the business? --------------------------------

_NO_REPLY_SENDER = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?|bounce)", re.IGNORECASE)


def automated_email_reason(headers: dict[str, str], from_address: str) -> str | None:
    """Why an email looks machine-sent (newsletter, notification, auto-reply), or None.

    `headers` keys are lower-case header names.
    """
    if "list-unsubscribe" in headers or "list-id" in headers:
        return "mailing list or newsletter"
    if headers.get("auto-submitted", "no").lower() != "no":
        return "auto-reply or automated message"
    if headers.get("precedence", "").lower() in {"bulk", "list", "junk", "auto_reply"}:
        return "bulk email"
    if any(h in headers for h in ("x-autoreply", "x-autorespond")):
        return "auto-reply"
    if _NO_REPLY_SENDER.match(from_address.split("@")[0]):
        return "sent from a no-reply address"
    return None


# --- logs: keep personal data out -----------------------------------------------------

_REDACTIONS = [
    (_EMAIL, "[email]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b"), "[iban]"),
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "[card]"),
    (_PHONE, "[phone]"),
    (re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE), "[postcode]"),
]


def redact(text: str) -> str:
    """Replace personal data (emails, phones, cards, IBANs, UK postcodes) with placeholders."""
    for pattern, placeholder in _REDACTIONS:
        text = pattern.sub(placeholder, text)
    return text
