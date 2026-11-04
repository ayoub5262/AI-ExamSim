import asyncio
from collections import OrderedDict
import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

# Automatically switch to local .venv interpreter if started with global Python lacking FastAPI
try:
    import fastapi  # noqa: F401
except ImportError:
    venv_py = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        sys.exit(subprocess.call([str(venv_py), *sys.argv]))
    raise

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import speech_recognition as sr
import uvicorn

from prof_agent import (
    API_KEY,
    AVAILABLE_MODELS,
    AVAILABLE_VOICES,
    BASE_DIR,
    EDGE_TTS_AVAILABLE,
    MODEL_NAME,
    PROTOKOLLE_DIR,
    VOICE_NAME,
    bewerten,
    build_skipped_evaluation,
    compute_exam_metrics,
    compute_topic_analytics,
    edge_tts,
    generate_exam_report,
    get_closing_remarks,
    group_questions_by_topic,
    load_questions,
    recognizer,
    select_questions_by_mode,
)

# ---------------------------------------------------------------------------
# 1. App Initialization & Directories
# ---------------------------------------------------------------------------
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
PROTOKOLLE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI-ExamSim Web UI", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory session store and bounded LRU audio cache
SESSIONS: dict[str, dict] = {}
TTS_CACHE: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
TTS_CACHE_LIMIT = 40


# ---------------------------------------------------------------------------
# 2. Request Schemas
# ---------------------------------------------------------------------------
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str | None = None
    rate: str = "+0%"


class ExamStartRequest(BaseModel):
    mode: str = "realistic"
    count: int = Field(default=5, ge=1, le=100)
    topic: str | None = None


class EvaluateRequest(BaseModel):
    session_id: str | None = None
    question_id: int
    user_antwort: str = ""
    frage: str | None = None
    musterloesung: str | None = None
    thema: str | None = None
    skip: bool = False
    model: str | None = None


class ExamFinishRequest(BaseModel):
    session_id: str | None = None
    mode_name: str | None = None
    results: list[dict] | None = None


# ---------------------------------------------------------------------------
# 3. Frontend & API Endpoints
# ---------------------------------------------------------------------------
@app.api_route("/", methods=["GET", "POST", "HEAD"], response_class=HTMLResponse)
@app.api_route("/index.html", methods=["GET", "POST", "HEAD"], response_class=HTMLResponse)
@app.api_route("/templates/index.html", methods=["GET", "POST", "HEAD"], response_class=HTMLResponse)
async def serve_spa():
    """Serve the Single-Page Application interface."""
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="templates/index.html not found.")
    return HTMLResponse(index_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


@app.get("/api/topics")
async def get_topics():
    """Return available topics, question counts, and runtime configuration."""
    fragen = load_questions()
    grouped = group_questions_by_topic(fragen)
    topic_counts = {name: len(items) for name, items in sorted(grouped.items())}

    return {
        "total_questions": len(fragen),
        "topics": [{"name": k, "count": v} for k, v in topic_counts.items()],
        "topic_counts": topic_counts,
        "config": {
            "model_name": MODEL_NAME,
            "voice_name": VOICE_NAME,
            "edge_tts_available": bool(EDGE_TTS_AVAILABLE and edge_tts),
            "api_configured": bool(API_KEY),
            "pause_tolerance_seconds": 3.0,
            "available_voices": AVAILABLE_VOICES,
            "available_models": AVAILABLE_MODELS,
        },
    }


async def _synthesize_tts_bytes(text: str, voice: str, rate: str = "+0%") -> bytes:
    """Synthesize speech into memory and store in TTS_CACHE."""
    key = (text, voice, rate)
    if key in TTS_CACHE:
        TTS_CACHE.move_to_end(key)
        return TTS_CACHE[key]

    buffer = bytearray()
    async for chunk in edge_tts.Communicate(text=text, voice=voice, rate=rate).stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            buffer.extend(chunk["data"])

    if not buffer:
        raise RuntimeError("Keine Audiodaten von edge-tts erhalten.")

    audio_bytes = bytes(buffer)
    TTS_CACHE[key] = audio_bytes
    if len(TTS_CACHE) > TTS_CACHE_LIMIT:
        TTS_CACHE.popitem(last=False)
    return audio_bytes


async def _prewarm_questions_tts(questions: list[dict], voice: str):
    """Pre-generate question audio in the background so playback is instant."""
    if not edge_tts:
        return
    for idx, q in enumerate(questions):
        prefix = "Willkommen zur mündlichen Prüfung. " if idx == 0 else ""
        speech_text = f"{prefix}Frage Nummer {idx + 1}: {q.get('frage', '')}".strip()
        try:
            await _synthesize_tts_bytes(speech_text, voice, "+0%")
        except Exception:
            pass


@app.get("/api/exam/start")
async def start_exam_get(mode: str = "realistic", count: int = 5, topic: str | None = None):
    return await start_exam(ExamStartRequest(mode=mode, count=count, topic=topic))


@app.post("/api/exam/start")
async def start_exam(payload: ExamStartRequest):
    """Initialize a new exam session based on the selected mode."""
    fragen = load_questions()
    try:
        questions, mode_name = select_questions_by_mode(fragen, payload.mode, payload.count, payload.topic)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "session_id": session_id,
        "mode_name": mode_name,
        "questions": {int(q["id"]): q for q in questions},
        "results": OrderedDict(),
    }

    # Pre-warm question TTS audio in background for zero-latency playback
    asyncio.create_task(_prewarm_questions_tts(questions, VOICE_NAME or "de-DE-KillianNeural"))

    return {
        "session_id": session_id,
        "mode": payload.mode,
        "mode_name": mode_name,
        "total_questions": len(questions),
        "questions": questions,
    }


@app.get("/api/tts")
async def text_to_speech_get(text: str, voice: str | None = None, rate: str = "+0%"):
    return await text_to_speech(TTSRequest(text=text, voice=voice, rate=rate))


@app.post("/api/tts")
async def text_to_speech(payload: TTSRequest):
    """Stream German neural speech audio via edge-tts directly from memory."""
    if not edge_tts:
        raise HTTPException(status_code=503, detail="edge-tts ist nicht installiert.")

    text = payload.text.strip()
    voice = (payload.voice or VOICE_NAME or "de-DE-KillianNeural").strip()
    try:
        audio_bytes = await _synthesize_tts_bytes(text, voice, payload.rate)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"TTS-Fehler: {exc}") from exc


@app.post("/api/evaluate")
async def evaluate_answer(payload: EvaluateRequest):
    """Evaluate a student's response and store the structured result in the session."""
    session = SESSIONS.get(payload.session_id) if payload.session_id else None
    q_id = int(payload.question_id)

    # Resolve question from active session, full question bank, or request payload
    q_obj = (session["questions"].get(q_id) if session else None) or next(
        (q for q in load_questions() if int(q.get("id", -1)) == q_id), None
    )
    if not q_obj and not (payload.frage and payload.musterloesung):
        raise HTTPException(status_code=404, detail=f"Frage #{q_id} nicht gefunden.")

    frage = (q_obj.get("frage") if q_obj else payload.frage) or ""
    musterloesung = (q_obj.get("musterloesung") if q_obj else payload.musterloesung) or ""
    thema = (q_obj.get("thema") if q_obj else payload.thema) or "Allgemein"
    user_antwort = (payload.user_antwort or "").strip()

    if payload.skip or not user_antwort:
        evaluation = build_skipped_evaluation()
    else:
        evaluation = await asyncio.to_thread(
            bewerten, frage, musterloesung, user_antwort, 2, payload.model
        )

    result = {
        "id": q_id,
        "thema": thema,
        "frage": frage,
        "musterloesung": musterloesung,
        "user_antwort": user_antwort,
        **evaluation,
        "points": evaluation["punkte"],
        "feedback": evaluation["gesprochenes_feedback"],
        "missing_aspects": evaluation["was_fehlt"],
        "key_takeaway": evaluation["merksatz"],
    }

    if session is not None:
        session["results"][q_id] = result
    return result


@app.post("/api/stt")
async def speech_to_text(audio: UploadFile = File(...), language: str = Form("de-DE")):
    """Optional server-side Speech-to-Text endpoint with Windows-safe temp file cleanup."""
    raw_bytes = await audio.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Leere Audiodatei empfangen.")

    suffix = Path(audio.filename or "recording.wav").suffix or ".wav"
    tmp_path: Path | None = None

    def _transcribe(path: Path) -> str:
        with sr.AudioFile(str(path)) as source:
            return recognizer.recognize_google(recognizer.record(source), language=language)

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw_bytes)
            tmp_path = Path(tmp.name)
        text = await asyncio.to_thread(_transcribe, tmp_path)
        return {"text": text, "language": language}
    except sr.UnknownValueError:
        return JSONResponse(status_code=422, content={"detail": "Sprache nicht verstanden.", "text": ""})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"STT-Fehler: {exc}") from exc
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@app.post("/api/exam/finish")
async def finish_exam(payload: ExamFinishRequest):
    """Compute final German grade, topic analytics, and save the Markdown transcript."""
    session = SESSIONS.get(payload.session_id) if payload.session_id else None
    mode_name = payload.mode_name or (session["mode_name"] if session else "Realistische Prüfung")
    results = payload.results or (list(session["results"].values()) if session else [])

    if not results:
        raise HTTPException(status_code=400, detail="Keine beantworteten Fragen zur Auswertung vorhanden.")

    total_pts, max_pts, pct, grade, grade_label = compute_exam_metrics(results)
    analytics = compute_topic_analytics(results)
    report_path = await asyncio.to_thread(generate_exam_report, results, mode_name)

    return {
        "session_id": payload.session_id,
        "mode_name": mode_name,
        "timestamp": datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "total_points": total_pts,
        "max_points": max_pts,
        "percentage": round(pct, 1),
        "grade": grade,
        "grade_label": grade_label,
        "passed": grade <= 4.0,
        "questions_total": len(results),
        "questions_answered": sum(1 for r in results if str(r.get("user_antwort", "")).strip()),
        "valid_questions_count": sum(1 for r in results if r.get("status") != "Fehler"),
        "strengths": analytics["strengths"],
        "weaknesses": analytics["weaknesses"],
        "topic_breakdown": analytics["topics"],
        "closing_remarks": get_closing_remarks(grade, grade_label),
        "results": results,
        "report_filename": report_path.name,
        "download_url": f"/api/protokolle/{report_path.name}",
    }


@app.get("/api/protokolle/{filename}")
@app.get("/protokolle/{filename}")
async def download_protocol(filename: str):
    """Serve a generated Markdown exam transcript for download."""
    safe_name = Path(filename).name
    file_path = PROTOKOLLE_DIR / safe_name
    if not safe_name.endswith(".md") or ".." in filename or not file_path.exists():
        raise HTTPException(status_code=404, detail="Prüfungsprotokoll nicht gefunden.")
    return FileResponse(str(file_path), media_type="text/markdown; charset=utf-8", filename=safe_name)


if __name__ == "__main__":
    print("\n" + "=" * 68)
    print("  AI-ExamSim Web UI startet auf: http://localhost:8000")
    print("=" * 68 + "\n")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
