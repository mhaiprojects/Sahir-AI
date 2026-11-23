"""Kokoro (sherpa-onnx 2.x) text prep: normalisation, lexicon lookup, OOV fallbacks.

Kokoro ignores espeak-ng and drops words missing from its lexicon files. This module
rewrites text so every token maps to lexicon-known material (or user phoneme entries).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_OOV_FALLBACK_COUNT = 0

_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_WWW_RE = re.compile(r"\bwww\.[^\s]+", re.IGNORECASE)
_VERSION_RE = re.compile(r"\b(v?\d+(?:\.\d+){1,4})\b", re.IGNORECASE)
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,10}\b")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[a-z]+)?|[^\s\w]", re.UNICODE)
_CAMEL_SPLIT = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_MIXED_ALNUM = re.compile(
    r"^[A-Za-z]*\d+[A-Za-z0-9]*$|^[A-Za-z]+\d.*$|^\d+[A-Za-z].*$"
)
_IPA_MARK = re.compile(r"[ˈˌːɑɹəɪʊɛɔæʌθðʃʒŋ]")


def kokoro_oov_fallback_total() -> int:
    return _OOV_FALLBACK_COUNT


def reset_kokoro_oov_fallback_count() -> None:
    global _OOV_FALLBACK_COUNT
    _OOV_FALLBACK_COUNT = 0


def bump_kokoro_oov_fallback(n: int = 1) -> None:
    global _OOV_FALLBACK_COUNT
    _OOV_FALLBACK_COUNT += n


@dataclass
class KokoroLexicon:
    """Word inventory from Kokoro lexicon files plus optional user overrides."""

    words: set[str] = field(default_factory=set)
    user_phoneme_words: set[str] = field(default_factory=set)
    respellings: dict[str, str] = field(default_factory=dict)

    def has(self, word: str) -> bool:
        w = word.lower().strip()
        if not w:
            return True
        if w in self.user_phoneme_words:
            return True
        return w in self.words

    def is_covered(self, word: str) -> bool:
        w = word.lower().strip()
        if not w:
            return True
        return self.has(w) or w in self.respellings

    @classmethod
    def load(
        cls,
        *lexicon_paths: Path | str,
        user_lexicon: Path | str | None = None,
    ) -> KokoroLexicon:
        self = cls()
        for path in lexicon_paths:
            p = Path(path)
            if not p.is_file():
                continue
            with p.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    word = line.split(None, 1)[0].lower()
                    if word:
                        self.words.add(word)
        if user_lexicon:
            up = Path(user_lexicon)
            if up.is_file():
                self._load_user_lexicon(up)
        return self

    def _load_user_lexicon(self, path: Path) -> None:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "->" in line:
                    left, right = line.split("->", 1)
                    word = left.strip().lower()
                    repl = right.strip()
                    if word and repl:
                        self.respellings[word] = repl
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                word = parts[0].lower()
                tail = " ".join(parts[1:])
                if _IPA_MARK.search(tail) or any(
                    tok in tail for tok in ("ˈ", "ˌ", "ɹ", "ə", "ɪ", "ʊ", "ɛ", "ɔ", "æ", "ʌ")
                ):
                    self.user_phoneme_words.add(word)
                    self.words.add(word)
                else:
                    self.respellings[word] = tail


def _digit_char_to_word(ch: str) -> str:
    return _DIGIT_WORDS.get(ch, ch)


def _speak_digits(digits: str) -> str:
    return " ".join(_digit_char_to_word(d) for d in digits)


def _speak_version(match: re.Match[str]) -> str:
    token = match.group(1)
    if token.lower().startswith("v") and token[1:].replace(".", "").isdigit():
        body = token[1:]
    else:
        body = token
    pieces: list[str] = []
    for ch in body:
        if ch == ".":
            pieces.append("point")
        elif ch.isdigit():
            pieces.append(_digit_char_to_word(ch))
        else:
            pieces.append(ch)
    return " ".join(pieces)


def _speak_url(match: re.Match[str]) -> str:
    url = match.group(0)
    url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    url = url.rstrip("/).,;")
    url = url.replace("/", " slash ")
    url = url.replace("-", " dash ")
    url = url.replace("_", " underscore ")
    url = url.replace(".", " dot ")
    url = url.replace(":", " ")
    url = url.replace("?", " ")
    url = url.replace("&", " and ")
    url = url.replace("=", " equals ")
    return re.sub(r"\s+", " ", url).strip()


def _speak_email(match: re.Match[str]) -> str:
    email = match.group(0)
    user, _, domain = email.partition("@")
    user_sp = " ".join(user.replace(".", " dot ").split())
    domain_sp = " ".join(domain.replace(".", " dot ").replace("-", " dash ").split())
    return f"{user_sp} at {domain_sp}"


def normalize_kokoro_text(text: str) -> str:
    """Expand URLs, emails, versions, and symbols into speakable plain text."""
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n")
    cleaned = _URL_RE.sub(_speak_url, cleaned)
    cleaned = _WWW_RE.sub(_speak_url, cleaned)
    cleaned = _EMAIL_RE.sub(_speak_email, cleaned)
    cleaned = _VERSION_RE.sub(_speak_version, cleaned)
    cleaned = cleaned.replace("&", " and ")
    cleaned = cleaned.replace("%", " percent ")
    cleaned = cleaned.replace("+", " plus ")
    cleaned = cleaned.replace("#", " hash ")
    cleaned = re.sub(r"\b3D\b", "three D", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("@", " at ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _split_mixed_alnum(word: str) -> list[str]:
    parts: list[str] = []
    buf = ""
    for ch in word:
        if ch.isdigit():
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(ch)
        else:
            buf += ch
    if buf:
        parts.append(buf)
    return parts


def _camel_parts(word: str) -> list[str]:
    return [p for p in _CAMEL_SPLIT.split(word) if p]


def _spell_letters(word: str) -> str:
    return " ".join(ch.upper() for ch in word if ch.isalpha())


def _resolve_word(word: str, lex: KokoroLexicon) -> tuple[str, bool]:
    """Return replacement text and whether a fallback was applied."""
    lower = word.lower()
    if lower in lex.respellings:
        logger.debug("Kokoro OOV fallback (user respelling): %s", word)
        return lex.respellings[lower], True

    if lex.has(lower):
        return word, False

    if word.isupper() and word.isalpha() and 2 <= len(word) <= 10:
        repl = " ".join(word)
        if all(lex.has(p.lower()) or len(p) == 1 for p in word):
            logger.debug("Kokoro OOV fallback (acronym): %s", word)
            return repl, True

    if _MIXED_ALNUM.match(word):
        spoken: list[str] = []
        for part in _split_mixed_alnum(word):
            if part.isdigit():
                spoken.append(_speak_digits(part))
            elif lex.has(part.lower()):
                spoken.append(part)
            elif part.isalpha():
                sub = _camel_parts(part)
                if len(sub) > 1 and all(lex.has(s.lower()) for s in sub):
                    spoken.extend(sub)
                else:
                    spoken.append(_spell_letters(part))
            else:
                spoken.append(part)
        repl = " ".join(spoken)
        logger.debug("Kokoro OOV fallback (alphanumeric): %s -> %s", word, repl)
        return repl, True

    if any(c.isupper() for c in word[1:]) and word.isalpha():
        parts = _camel_parts(word)
        if len(parts) > 1 and all(lex.has(p.lower()) for p in parts):
            repl = " ".join(parts)
            logger.debug("Kokoro OOV fallback (camelCase): %s", word)
            return repl, True

    if word.isalpha():
        # Try splitting known prefix/suffix heuristics (ClickUp -> click up).
        for i in range(3, len(word) - 1):
            left, right = word[:i], word[i:]
            if lex.has(left.lower()) and lex.has(right.lower()):
                repl = f"{left} {right}"
                logger.debug("Kokoro OOV fallback (split): %s", word)
                return repl, True

    if word.isalpha() and len(word) <= 24:
        repl = _spell_letters(word)
        logger.debug("Kokoro OOV fallback (spell): %s", word)
        return repl, True

    logger.debug("Kokoro OOV unresolved token kept: %s", word)
    return word, True


def prepare_kokoro_text(text: str, lex: KokoroLexicon, *, count_fallbacks: bool = True) -> str:
    """Normalise and rewrite text so Kokoro should not drop tokens silently."""
    normalised = normalize_kokoro_text(text)
    if not normalised:
        return ""

    out: list[str] = []
    fallbacks = 0

    def _emit_prepared(segment: str) -> None:
        nonlocal fallbacks
        for match in _TOKEN_RE.finditer(segment):
            tok = match.group(0)
            if re.fullmatch(r"[A-Za-z0-9]+(?:'[a-z]+)?", tok):
                repl, used = _resolve_word(tok, lex)
                if used:
                    fallbacks += 1
                out.append(repl)
            else:
                out.append(tok)

    # Expand standalone acronyms in running text (GPU -> G P U) before token pass.
    def _acronym_sub(m: re.Match[str]) -> str:
        w = m.group(0)
        if lex.has(w.lower()):
            return w
        return " ".join(w)

    expanded = _ACRONYM_RE.sub(_acronym_sub, normalised)
    _emit_prepared(expanded)

    merged = re.sub(r"\s+", " ", " ".join(out)).strip()
    if count_fallbacks and fallbacks:
        bump_kokoro_oov_fallback(fallbacks)
    return merged


def collect_oov_words(text: str, lex: KokoroLexicon) -> list[str]:
    """Words in *text* absent from *lex* before fallback rewriting."""
    normalised = normalize_kokoro_text(text)
    oov: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"[A-Za-z0-9]+(?:'[a-z]+)?", normalised):
        w = match.group(0)
        if not lex.is_covered(w.lower()) and w.lower() not in seen:
            seen.add(w.lower())
            oov.append(w)
    return oov


def default_user_lexicon_path() -> Path:
    from config import PROJECT_ROOT

    return PROJECT_ROOT / "models/kokoro/user_lexicon.txt"