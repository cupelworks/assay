# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""METEOR: how much of the expected output the answer covers, counting other
forms of a word and synonyms as matches.

Scored with NLTK's METEOR (`nltk`, in the `nlp` extra), which matches words
in three steps — the same word, the same stem ("arrive"/"arrives"), then a
WordNet synonym ("choose"/"select") — and combines precision and recall,
weighted towards recall, with a penalty when the matched words are out of
order. Row settings `alpha`, `beta` and `gamma` are NLTK's parameters
(0.9, 3.0 and 0.5 by default). The score is on METEOR's native 0–1 scale
and passes against the assignment's `threshold` in the direction the row's
`comparison` declares; an identical answer scores just under 1 (0.9999).

NLTK's synonym step runs on the words the stem step left over, already
stemmed, and a stem is often not a dictionary word ("purchase" → "purchas"),
so most synonyms would never match. The synonym dictionary given to it here
answers for stems instead: every synonym, stemmed, of every WordNet word
with that stem. Scores are therefore higher than stock NLTK METEOR's when
synonyms occur.

WordNet is NLTK data, not part of the package: `python -m nltk.downloader
wordnet`. Loading it is not safe from several threads at once, so it happens
under a lock, once per process — at worker start-up through `warm_up`, or on
the first METEOR check. Word forms and synonyms are English: in other
languages mostly exact words match.
"""
import logging
import re
import threading
from collections import defaultdict
from functools import cache
from typing import Any

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators._common import against_threshold, require_reference

logger = logging.getLogger(__name__)

# Words and punctuation marks, so "days." still matches "days"
_WORD = re.compile(r"\w+|[^\w\s]")
_PARAMETERS = ("alpha", "beta", "gamma")
_NOT_INSTALLED = "METEOR isn't installed on this worker: it needs the nlp extra (the nltk package)"
_NO_WORDNET = ("METEOR's WordNet data isn't installed on this worker: download it with "
               "python -m nltk.downloader wordnet")

_lock = threading.Lock()
_synonyms: "_StemmedWordNet | None" = None


def evaluate(evaluation: EvaluationInput) -> TestTypeResult:
    reference = require_reference(evaluation)
    parameters = _parameters(evaluation.engine_settings)
    try:
        from nltk.translate.meteor_score import meteor_score
    except ImportError:
        raise ValueError(_NOT_INSTALLED) from None

    synonyms = _synonym_dictionary()
    score = meteor_score(
        [_words(reference)], _words(evaluation.answer),
        stemmer=synonyms.stemmer, wordnet=synonyms, **parameters,
    )
    return against_threshold(score, evaluation)


def warm_up() -> None:
    """Load WordNet and score once, before any task thread can: called when a
    worker starts. A worker without nltk skips it; one without the WordNet
    data logs why its METEOR checks will fail, and starts anyway."""
    try:
        from nltk.translate.meteor_score import meteor_score
    except ImportError:
        return
    try:
        synonyms = _synonym_dictionary()
    except ValueError as exc:
        logger.warning("METEOR checks will fail on this worker: %s", exc)
        return
    meteor_score([["ready"]], ["ready"], stemmer=synonyms.stemmer, wordnet=synonyms)
    logger.info("METEOR ready: WordNet loaded")


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _parameters(settings: dict[str, Any]) -> dict[str, float]:
    parameters = {}
    for name in _PARAMETERS:
        if name not in settings:
            continue
        try:
            parameters[name] = float(settings[name])
        except (TypeError, ValueError):
            raise ValueError(
                f"METEOR setting {name} {settings[name]!r} is not a number on this test "
                "type's catalogue row"
            ) from None
    return parameters


def _synonym_dictionary() -> "_StemmedWordNet":
    global _synonyms
    with _lock:
        if _synonyms is None:
            from nltk.corpus import wordnet
            from nltk.stem import PorterStemmer
            try:
                _synonyms = _StemmedWordNet(wordnet, PorterStemmer())
            except LookupError:
                raise ValueError(_NO_WORDNET) from None
        return _synonyms


class _StemmedWordNet:
    """WordNet as NLTK's METEOR synonym step uses it — `synsets(word)`, then
    each synset's `lemmas()` and each lemma's `name()` — but looked up by
    stem, answering with stems."""

    def __init__(self, wordnet: Any, stemmer: Any) -> None:
        self.stemmer = stemmer
        self._wordnet = wordnet
        self._words_by_stem: dict[str, set[str]] = defaultdict(set)
        for name in wordnet.all_lemma_names():
            if "_" not in name:
                self._words_by_stem[stemmer.stem(name)].add(name)

    def synsets(self, stem: str) -> list["_Synset"]:
        return [_Synset(self._synonym_stems(stem))]

    @cache  # noqa: B019 — one instance per process, kept for the process's life
    def _synonym_stems(self, stem: str) -> tuple[str, ...]:
        stems = set()
        for word in self._words_by_stem.get(stem, {stem}):
            for synset in self._wordnet.synsets(word):
                for lemma in synset.lemmas():
                    if "_" not in lemma.name():
                        stems.add(self.stemmer.stem(lemma.name()))
        return tuple(stems)


class _Synset:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._lemmas = [_Lemma(name) for name in names]

    def lemmas(self) -> list["_Lemma"]:
        return self._lemmas


class _Lemma:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name
