# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Models the scoring engines load once per process and share.

A worker runs its tasks in threads (`--pool=threads`), so a model is loaded
the first time an engine asks for it — two threads asking at once load it
once — and kept for the life of the process. Each model comes with its own
lock, which the engine holds while scoring: a Hugging Face fast tokenizer
raises "Already borrowed" when one instance is used from two threads at
once. Scoring one pair takes a few milliseconds, so waiting is cheap.

Models are Hugging Face downloads, cached under `HF_HOME` (by default
`~/.cache/huggingface`). The worker image downloads them at build time and
runs offline (`HF_HUB_OFFLINE=1`); locally, the first check with a model
downloads it.
"""
import threading
from collections.abc import Callable
from typing import Any

_models: dict[tuple[str, str], tuple[Any, threading.Lock]] = {}
_loading: dict[tuple[str, str], threading.Lock] = {}
_registry_lock = threading.Lock()


def get(kind: str, name: str, load: Callable[[str], Any]) -> tuple[Any, threading.Lock]:
    """The model of this kind and name, loaded with `load(name)` the first
    time it's asked for, and the lock to hold while using it.

    Raises:
        ValueError: the model couldn't be loaded — not downloaded on an
            offline worker, or no such model — with the reason.
    """
    key = (kind, name)
    loaded = _models.get(key)
    if loaded is not None:
        return loaded
    with _registry_lock:
        loading = _loading.setdefault(key, threading.Lock())
    with loading:
        loaded = _models.get(key)
        if loaded is None:
            _quiet_hugging_face()
            try:
                model = load(name)
            except ImportError:
                raise
            except Exception as exc:
                raise ValueError(
                    f"The model {name} couldn't be loaded on this worker: {exc}"
                ) from None
            loaded = _models[key] = (model, threading.Lock())
    return loaded


def _quiet_hugging_face() -> None:
    """Loading prints progress bars and a load report to the worker's output
    otherwise; failures still raise."""
    try:
        from huggingface_hub.utils import disable_progress_bars
        from huggingface_hub.utils import logging as hub_logging
        from transformers.utils import logging as transformers_logging
    except ImportError:
        return
    disable_progress_bars()
    hub_logging.set_verbosity_error()
    transformers_logging.set_verbosity_error()
    transformers_logging.disable_progress_bar()
