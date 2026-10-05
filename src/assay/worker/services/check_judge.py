# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging
import uuid

from sqlalchemy.orm import Session

from assay.messages import sentence
from assay.models import JudgeCheckModel
from assay.schemas.settings import JudgeSettings
from assay.worker import llm
from assay.worker.evaluators.engines import llm_judge
from assay.worker.services._checks import EXPIRED, claim, complete, expired, fail

logger = logging.getLogger(__name__)

# The one question a check puts to the judge: small, cheap, and with an
# obvious verdict, so what's checked is whether a readable verdict comes back.
QUESTION = "What is the capital of France?"
ANSWER = "Paris is the capital of France."
RUBRIC = ("Judge whether the answer addresses the question that was actually asked: pass if "
          "it responds to that request; fail if it is off-topic.")


def check_judge(check_id: uuid.UUID, session: Session) -> None:
    """Runs one check of the judge settings: claim it, ask the judge the
    fixed question with the settings stored on the check, write the outcome.

    The same prompt and client a judge check in a run uses, one attempt, no
    retries — someone is waiting. `ok` means a readable verdict came back,
    whatever it said; `answer` is the judge's rationale. Anything that goes
    wrong once the check is claimed completes it with the reason.

    Args:
        check_id: UUID of the JudgeCheckModel to run.
        session: Active sync SQLAlchemy session (assay.worker.db).
    """
    check = claim(session, JudgeCheckModel, check_id)
    if check is None:
        logger.info("Judge check %s not claimed: missing, already running or already "
                    "completed", check_id)
        return
    try:
        _ask(check, session)
    except Exception as exc:
        logger.exception("Judge check %s failed on the worker", check_id)
        fail(session, check, exc)


def _ask(check: JudgeCheckModel, session: Session) -> None:
    check_id = check.id
    if expired(check):
        complete(check, ok=False, error=EXPIRED)
        session.commit()
        logger.warning("Judge check %s expired before a worker picked it up", check_id)
        return

    settings = JudgeSettings.model_validate(check.settings).model_copy(update={"max_retries": 0})
    try:
        verdict = llm.ask_for_verdict(
            settings, llm_judge.SYSTEM_PROMPT, llm_judge.build_prompt(RUBRIC, QUESTION, ANSWER),
        )
    except llm.JudgeError as exc:
        complete(check, ok=False, error=sentence(str(exc)), status_code=exc.status,
                 latency_ms=exc.latency_ms)
    else:
        complete(check, ok=True, answer=verdict.rationale, status_code=verdict.status,
                 latency_ms=verdict.latency_ms)
    session.commit()

    logger.info(
        "Judge check %s completed: %s (%s)", check_id, "ok" if check.ok else "failed",
        f"HTTP {check.status_code}" if check.status_code else "no response",
        extra={"ok": check.ok, "status": check.status_code, "latency_ms": check.latency_ms},
    )
