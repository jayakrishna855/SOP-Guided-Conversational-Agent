import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.model.client import KeyCheckError, ModelAdapter, OpenAIModel
from app.workflow.engine import TurnResult, allowed_actions
from app.workflow.state import Session

router = APIRouter(prefix="/api")

MAX_MESSAGE_CHARS = 1000  # also the textarea's maxlength in static/index.html
_KEY_SHAPE = re.compile(r"[A-Za-z0-9_-]{20,300}")

SESSION_NOT_FOUND = "This session has expired or doesn't exist. Please start a new session."
ACTION_NOT_ALLOWED = "That option isn't available right now."
KEY_REQUIRED = "Enter your OpenAI API key to start chatting."
KEY_MALFORMED = "That doesn't look like an OpenAI API key."
MESSAGE_TOO_LONG = f"Please keep messages under {MAX_MESSAGE_CHARS} characters."


class SessionRequest(BaseModel):
    # Optional when the server has its own OPENAI_API_KEY. Never logged or returned.
    api_key: str | None = Field(default=None, max_length=300, repr=False)


class ChatRequest(BaseModel):
    session_id: str = Field(max_length=64)
    message: str = Field(min_length=1, max_length=20_000)  # hard cap; the friendly limit is checked below


class ActionRequest(BaseModel):
    session_id: str = Field(max_length=64)
    action: Literal["send_summary", "skip_summary", "reset"]


class SentEmailOut(BaseModel):
    to: str  # masked
    subject: str
    body: str


class TurnResponse(BaseModel):
    reply: str
    phase: str
    allowed_actions: list[str]
    summary_preview: str | None = None  # shown with the Send summary / Skip buttons
    outbox: list[SentEmailOut] = []  # simulated emails sent in this session


class SessionResponse(TurnResponse):
    session_id: str


def _response(request: Request, session: Session, result: TurnResult) -> dict:
    outbox = request.app.state.services.outbox.for_session(session.id)
    return {
        "reply": result.reply,
        "phase": result.phase.value,
        "allowed_actions": result.allowed_actions,
        "summary_preview": result.summary_preview,
        "outbox": [vars(m) for m in outbox],
    }


def _new_session(request: Request, model: ModelAdapter) -> dict:
    session = request.app.state.store.create(model)
    with request.app.state.store.checkout(session.id) as checked_out:
        result = request.app.state.engine.start(checked_out)
    return {"session_id": session.id, **_response(request, session, result)}


def _model_for(request: Request, api_key: str | None) -> ModelAdapter:
    """The caller's own key if they gave one (checked with one tiny call), else the server's."""
    key = (api_key or "").strip()
    if not key:
        if request.app.state.server_model is None:
            raise HTTPException(status_code=401, detail=KEY_REQUIRED)
        return request.app.state.server_model
    if not _KEY_SHAPE.fullmatch(key):
        raise HTTPException(status_code=400, detail=KEY_MALFORMED)
    model = OpenAIModel(request.app.state.settings, key)
    try:
        model.check_key()
    except KeyCheckError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    return model


@router.post("/session", response_model=SessionResponse)
def create_session(request: Request, body: SessionRequest | None = None) -> dict:
    return _new_session(request, _model_for(request, body.api_key if body else None))


@router.post("/chat", response_model=TurnResponse)
def chat(body: ChatRequest, request: Request) -> dict:
    if len(body.message) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail=MESSAGE_TOO_LONG)
    store = request.app.state.store
    with store.checkout(body.session_id) as session:
        model = store.model(body.session_id) if session is not None else None
        if session is None or model is None:
            raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
        result = request.app.state.engine.handle_turn(session, body.message, model)
        return _response(request, session, result)


@router.post("/action", response_model=SessionResponse | TurnResponse)
def action(body: ActionRequest, request: Request) -> dict:
    """Button actions, checked against the session's allowed_actions on the server.
    `reset` discards the session and starts a new one with the same key."""
    store = request.app.state.store
    with store.checkout(body.session_id) as session:
        if session is None:
            raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
        if body.action not in allowed_actions(session):
            raise HTTPException(status_code=409, detail=ACTION_NOT_ALLOWED)
        if body.action != "reset":
            result = request.app.state.engine.handle_action(session, body.action)
            return _response(request, session, result)
        model = store.model(body.session_id)
    store.discard(body.session_id)
    if model is None:  # expired in the meantime
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND)
    return _new_session(request, model)
