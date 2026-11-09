"""Persistence: students, problems, the conversation, and every recorded mistake."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(32)
    )
    locale: Mapped[str] = mapped_column(String(5), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    problems: Mapped[list[Problem]] = relationship(
        back_populates="student", order_by="Problem.sequence"
    )
    mistakes: Mapped[list[Mistake]] = relationship(
        back_populates="student", order_by="Mistake.sequence"
    )


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    equation: Mapped[str] = mapped_column(String(200))
    skill: Mapped[str] = mapped_column(String(40))
    topic: Mapped[str] = mapped_column(String(20), default="linear")
    difficulty: Mapped[int] = mapped_column(Integer, default=1)
    origin: Mapped[str] = mapped_column(String(20))  # seed | custom | model | generator
    purpose: Mapped[str] = mapped_column(String(20), default="practice")  # practice | follow_up
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|completed|abandoned
    phase: Mapped[str] = mapped_column(String(20), default="solving")  # solving|correcting|checking
    last_valid: Mapped[str] = mapped_column(String(200))
    wrong_equation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hint_level: Mapped[int] = mapped_column(Integer, default=0)
    check_attempts: Mapped[int] = mapped_column(Integer, default=0)
    mistake_count: Mapped[int] = mapped_column(Integer, default=0)
    check_verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    student: Mapped[Student] = relationship(back_populates="problems")
    messages: Mapped[list[Message]] = relationship(
        back_populates="problem", order_by="Message.sequence"
    )


class Mistake(Base):
    __tablename__ = "mistakes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    skill: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(20))  # engine | model
    equation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    student: Mapped[Student] = relationship(back_populates="mistakes")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    problem: Mapped[Problem] = relationship(back_populates="messages")


def make_session_factory(database_url: str) -> sessionmaker:
    url = make_url(database_url)
    options: dict = {}
    if url.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        else:
            # An in-memory database exists per connection, so all threads must share one.
            options["poolclass"] = StaticPool
    engine = create_engine(database_url, **options)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, autoflush=False)
