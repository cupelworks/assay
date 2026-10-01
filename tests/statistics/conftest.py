"""A real database for the statistics tests: the statistics services fold runs
from several tables together, which a mocked session can't show. A template is
migrated once per test session (the catalogue of check types is seeded by the
migrations), and every test gets its own copy, an API client bound to it, and
a worker-side session to execute runs in-process. Dispatch to the broker is
captured, never sent."""
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from assay.config import settings
from assay.db import get_session
from assay.main import create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def template_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("statistics") / "template.db"
    previous = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{path}"
    try:
        config = Config()
        config.set_main_option("script_location", str(ROOT / "alembic"))
        command.upgrade(config, "head")
    finally:
        settings.database_url = previous
    return path


def _foreign_keys_on(sync_engine):
    @event.listens_for(sync_engine, "connect")
    def _on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@dataclass
class Database:
    client: TestClient
    worker_session: sessionmaker
    dispatched: list[uuid.UUID] = field(default_factory=list)

    # -- seeding through the API, as a user would --

    def test(self, name="t", input="q", model_output="answer", expected_output="answer",
             checks=None) -> dict:
        checks = checks if checks is not None else [{"name": "Contains",
                                                      "config": {"substring": "answer"}}]
        response = self.client.post("/tests", json={
            "name": name, "input": input, "model_output": model_output,
            "expected_output": expected_output, "test_type_assignments": checks})
        assert response.status_code in (200, 201), response.text
        return response.json()

    def test_set(self, name, tests) -> dict:
        response = self.client.post("/test-sets", json={"name": name})
        assert response.status_code in (200, 201), response.text
        test_set = response.json()
        if tests:
            added = self.client.post(f"/test-sets/{test_set['id']}/entries",
                                     json=[{"id": t["id"]} for t in tests])
            assert added.status_code in (200, 201), added.text
        return test_set

    # -- the batch's runs, worker-side --

    def runs_of(self, batch_id) -> list:
        """The batch's runs as rows (id, batch_index, status, ...), in time order."""
        from sqlalchemy import select

        from assay.models import TestRunModel
        with self.worker_session() as session:
            return session.execute(
                select(TestRunModel.id, TestRunModel.batch_index, TestRunModel.status,
                       TestRunModel.error, TestRunModel.test_set_execution_id,
                       TestRunModel.test_plan_execution_id, TestRunModel.test_id)
                .where(TestRunModel.batch_id == uuid.UUID(str(batch_id)))
                .order_by(TestRunModel.batch_index)).all()

    def execute(self, run_ids) -> None:
        """Execute runs in-process, as a worker would."""
        from assay.worker.services.execute_run import execute_run
        for run_id in run_ids:
            with self.worker_session() as session:
                execute_run(run_id, session)

    def set_status(self, run_id, status) -> None:
        from sqlalchemy import update

        from assay.models import TestRunModel
        with self.worker_session() as session:
            session.execute(update(TestRunModel).where(TestRunModel.id == run_id)
                            .values(status=status))
            session.commit()

    def test_plan(self, name, test_sets) -> dict:
        response = self.client.post("/test-plans", json={"name": name})
        assert response.status_code in (200, 201), response.text
        plan = response.json()
        if test_sets:
            linked = self.client.post(f"/test-plans/{plan['id']}/entries",
                                      json=[{"id": s["id"]} for s in test_sets])
            assert linked.status_code in (200, 201), linked.text
        return plan


@pytest.fixture
def db(template_db, tmp_path, monkeypatch) -> Database:
    path = tmp_path / "statistics.db"
    shutil.copy(template_db, path)
    url = f"sqlite+aiosqlite:///{path}"

    async_engine = create_async_engine(url)
    _foreign_keys_on(async_engine.sync_engine)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    sync_engine = create_engine(f"sqlite:///{path}")
    _foreign_keys_on(sync_engine)

    app = create_app()

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    database = Database(client=TestClient(app), worker_session=sessionmaker(sync_engine))
    # every publish goes through the one Celery app's send_task: capture it there
    from assay.worker import app as celery_app

    def send_task(name, args=None, **_):
        if name.endswith("execute_run"):
            database.dispatched.extend(args)

    monkeypatch.setattr(celery_app, "send_task", send_task)
    yield database
    sync_engine.dispose()
