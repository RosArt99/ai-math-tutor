"""HTTP API. Tutoring logic lives in ``tutor``; every client (web, iOS, Android) calls this."""

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import curriculum, tutor
from .config import Settings, get_settings
from .llm import LLMClient, LLMError, build_client
from .models import Student, make_session_factory

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

Locale = Literal["en", "ru"]
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class CreateStudentRequest(BaseModel):
    locale: Locale = "en"


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)
    locale: Locale | None = None


class ProblemRequest(BaseModel):
    equation: str = Field(min_length=1, max_length=200)


class LocaleRequest(BaseModel):
    locale: Locale


class _TurnGuard:
    """Per-student serialisation and rate limiting for a single-process deployment.

    A multi-instance deployment would move both to Redis; see docs/SCALING.md.
    """

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self.turns: defaultdict[str, deque[float]] = defaultdict(deque)
        self.mutex = threading.Lock()

    def lock_for(self, student_id: str) -> threading.Lock:
        with self.mutex:
            return self.locks[student_id]

    def allow(self, student_id: str) -> bool:
        now = time.monotonic()
        with self.mutex:
            recent = self.turns[student_id]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= self.per_minute:
                return False
            recent.append(now)
            return True


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    config = settings or get_settings()
    client = llm or build_client(config)
    session_factory: sessionmaker = make_session_factory(config.database_url)
    guard = _TurnGuard(config.max_turns_per_minute)
    app = FastAPI(title="AI Math Tutor", version="0.1.0")

    def get_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    Db = Annotated[Session, Depends(get_db)]

    def get_student(db: Db, x_student_token: Annotated[str, Header()] = "") -> Student:
        student = db.scalar(select(Student).where(Student.token == x_student_token))
        if student is None:
            raise HTTPException(401, detail={"code": "unknown_student"})
        return student

    CurrentStudent = Annotated[Student, Depends(get_student)]

    @app.exception_handler(LLMError)
    def llm_error(_: Request, error: LLMError) -> JSONResponse:
        logger.warning("LLM failure (%s): %s", error.code, error)
        status = 429 if error.code in ("daily_limit", "rate_limited") else 503
        return JSONResponse(status_code=status, content={"detail": {"code": f"llm_{error.code}"}})

    @app.get("/api/health")
    def health() -> dict:
        configured = client.label == "stub" or bool(config.openrouter_api_key)
        return {
            "status": "ok",
            "provider": client.label,
            "configured": configured,
            "models": config.model_list if client.label != "stub" else ["stub"],
        }

    @app.post("/api/students", status_code=201)
    def create_student(body: CreateStudentRequest, db: Db) -> dict:
        student = tutor.create_student(db, body.locale)
        return {"token": student.token, **tutor.state_view(student)}

    @app.get("/api/state")
    def get_state(student: CurrentStudent) -> dict:
        return tutor.state_view(student)

    @app.put("/api/locale")
    def set_locale(body: LocaleRequest, student: CurrentStudent, db: Db) -> dict:
        student.locale = body.locale
        db.commit()
        return tutor.state_view(student)

    @app.post("/api/messages")
    def send_message(body: MessageRequest, student: CurrentStudent, db: Db) -> dict:
        text = body.text.strip()
        if not text:
            raise HTTPException(422, detail={"code": "empty_message"})
        if len(text) > config.max_message_length:
            raise HTTPException(422, detail={"code": "message_too_long"})
        if not guard.allow(student.id):
            raise HTTPException(429, detail={"code": "too_many_messages"})
        if body.locale:
            student.locale = body.locale
        lock = guard.lock_for(student.id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, detail={"code": "turn_in_progress"})
        try:
            outcome = tutor.handle_turn(db, client, student, text, config.history_messages)
        except LLMError:
            db.rollback()
            raise
        finally:
            lock.release()
        return {**outcome, **tutor.state_view(student)}

    @app.post("/api/topics/{topic_id}/start", status_code=201)
    def start_topic(topic_id: str, student: CurrentStudent, db: Db) -> dict:
        if topic_id not in curriculum.TOPICS:
            raise HTTPException(404, detail={"code": "unknown_topic"})
        tutor.start_topic(db, student, topic_id)
        db.refresh(student)
        return tutor.state_view(student)

    @app.post("/api/problems", status_code=201)
    def start_problem(body: ProblemRequest, student: CurrentStudent, db: Db) -> dict:
        error = tutor.custom_problem_error(body.equation)
        if error:
            raise HTTPException(422, detail={"code": error})
        tutor.start_custom_problem(db, student, body.equation)
        db.refresh(student)
        return tutor.state_view(student)

    if FRONTEND_DIST.exists():
        app.mount(
            "/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets"
        )

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (FRONTEND_DIST / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app
