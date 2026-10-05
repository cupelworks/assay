# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""One module per engine, each exposing evaluate(EvaluationInput) -> TestTypeResult.

An engine is a pure function of its EvaluationInput: no database, no ORM
model, no knowledge of which run or which frozen copy it's scoring. It
reads the assignment's values from evaluation.config and the catalogue
row's parameters from evaluation.engine_settings; the registry
(evaluators/registry.py) is the only thing that maps a row's engine name
to one of these.
"""
