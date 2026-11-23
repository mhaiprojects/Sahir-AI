"""Kokoro OOV normalisation, lexicon, and health counter."""

from __future__ import annotations

from pathlib import Path

import pytest

from kokoro_speech import (
    KokoroLexicon,
    collect_oov_words,
    kokoro_oov_fallback_total,
    normalize_kokoro_text,
    prepare_kokoro_text,
    reset_kokoro_oov_fallback_count,
)
from tts_backends import kokoro_tts_health


@pytest.fixture
def stub_lexicon(tmp_path: Path) -> KokoroLexicon:
    lex_file = tmp_path / "lexicon-us-en.txt"
    lex_file.write_text(
        "hello h ə l ˈ O\nclick k l ˈ ɪ k\nup ˌ ʌ p\nfive f ˈ I v\nzero z ˈ ɪ ɹ O\n",
        encoding="utf-8",
    )
    user = tmp_path / "user_lexicon.txt"
    user.write_text("sahir s ə ˈ h i ɹ\nollama o l l a m a\n", encoding="utf-8")
    return KokoroLexicon.load(lex_file, user_lexicon=user)


def test_normalize_acronyms_urls_versions():
    text = "GPU v2.0 at https://ex.com and a@b.co"
    out = normalize_kokoro_text(text)
    assert "ex dot com" in out.lower() or "ex" in out.lower()
    assert " at " in out
    assert "point" in out.lower()


def test_acronym_and_spell_fallback(stub_lexicon: KokoroLexicon):
    oov = collect_oov_words("My PC needs a GPU", stub_lexicon)
    assert "pc" in {w.lower() for w in oov}
    prepared = prepare_kokoro_text("My PC needs a GPU", stub_lexicon)
    assert "P" in prepared and "C" in prepared
    assert collect_oov_words(prepared, stub_lexicon) == [] or "pc" not in {
        w.lower() for w in collect_oov_words(prepared, stub_lexicon)
    }


def test_user_lexicon_phoneme_word(stub_lexicon: KokoroLexicon):
    prepared = prepare_kokoro_text("Sahir uses Ollama", stub_lexicon)
    assert "Sahir" in prepared or "sahir" in prepared.lower()
    assert stub_lexicon.has("sahir")


def test_camel_and_mixed_alnum(stub_lexicon: KokoroLexicon):
    prepared = prepare_kokoro_text("ClickUp RX5500", stub_lexicon)
    assert "click" in prepared.lower()
    assert "five" in prepared.lower() or "5" in prepared


def test_no_word_dropped_token_coverage(stub_lexicon: KokoroLexicon):
    src = "PC GPU Sahir Ollama ClickUp RX5500 v3.1.4"
    prepared = prepare_kokoro_text(src, stub_lexicon)
    assert prepared.strip()
    for raw in src.split():
        token = raw.strip("v")
        if token.isalpha() or any(c.isalpha() for c in token):
            frag = token[0].lower()
            assert frag in prepared.lower()


def test_health_counter_increments(stub_lexicon: KokoroLexicon):
    reset_kokoro_oov_fallback_count()
    prepare_kokoro_text("PC GPU", stub_lexicon)
    assert kokoro_oov_fallback_total() >= 2
    health = kokoro_tts_health()
    assert health["kokoro_oov_fallbacks"] >= 2
    assert health["default_num_threads"] >= 1


def test_respelling_arrow_syntax(tmp_path: Path):
    user = tmp_path / "user.txt"
    user.write_text("clickup -> click up\n", encoding="utf-8")
    lex = KokoroLexicon.load(tmp_path / "missing.txt", user_lexicon=user)
    assert lex.respellings["clickup"] == "click up"