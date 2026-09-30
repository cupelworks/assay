import threading
import time

import pytest

from assay.worker.evaluators import models


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(models, "_models", {})
    monkeypatch.setattr(models, "_loading", {})


def test_a_model_is_loaded_once_and_then_shared():
    loads = []

    def load(name):
        loads.append(name)
        return f"model {name}"

    first = models.get("kind", "m", load)
    second = models.get("kind", "m", load)

    assert first == second
    assert first[0] == "model m"
    assert loads == ["m"]


def test_two_threads_asking_at_once_load_it_once():
    loads = []

    def slow_load(name):
        time.sleep(0.05)
        loads.append(name)
        return object()

    results = []
    threads = [threading.Thread(target=lambda: results.append(models.get("kind", "m", slow_load)))
               for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(loads) == 1
    assert len({id(result[0]) for result in results}) == 1


def test_each_model_has_its_own_lock():
    _, lock_a = models.get("kind", "a", lambda name: name)
    _, lock_b = models.get("kind", "b", lambda name: name)

    assert lock_a is not lock_b


def test_the_same_name_of_another_kind_is_another_model():
    assert models.get("one", "m", lambda name: "one")[0] == "one"
    assert models.get("two", "m", lambda name: "two")[0] == "two"


def test_a_model_that_cannot_load_is_the_checks_failure_with_the_reason():
    def fails(name):
        raise OSError("not in the cache and the worker is offline")

    with pytest.raises(ValueError, match="The model m couldn't be loaded on this worker: not in "
                                         "the cache and the worker is offline"):
        models.get("kind", "m", fails)


def test_a_failed_load_is_retried_next_time():
    attempts = []

    def flaky(name):
        attempts.append(name)
        if len(attempts) == 1:
            raise OSError("network")
        return "model"

    with pytest.raises(ValueError):
        models.get("kind", "m", flaky)

    assert models.get("kind", "m", flaky)[0] == "model"


def test_a_missing_library_is_left_to_the_engine():
    def no_library(name):
        raise ImportError("sentence_transformers")

    with pytest.raises(ImportError):
        models.get("kind", "m", no_library)
