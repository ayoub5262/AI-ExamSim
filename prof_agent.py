import asyncio
import datetime
import json
import os
from pathlib import Path
import random
import re
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI
import speech_recognition as sr

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

# Ensure UTF-8 console output on Windows to prevent charmap UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import edge_tts
    import pygame
    EDGE_TTS_AVAILABLE = True
except ImportError:
    edge_tts = None
    EDGE_TTS_AVAILABLE = False

# ---------------------------------------------------------------------------
# 1. Configuration & OpenAI Client
# ---------------------------------------------------------------------------
load_dotenv(override=True)
BASE_DIR = Path(__file__).resolve().parent
PROTOKOLLE_DIR = BASE_DIR / "protokolle"

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://chat.kiconnect.nrw/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "OpenAI GPT OSS 120B")
VOICE_NAME = os.getenv("VOICE_NAME", "de-DE-KillianNeural")

AVAILABLE_MODELS = [
    {"id": "OpenAI GPT OSS 120B", "label": "GPT OSS 120B (Turbo ~1s)"},
    {"id": "qwen3.8-27b", "label": "Qwen 3.8 27B"},
]

AVAILABLE_VOICES = [
    {"id": "de-DE-KillianNeural", "label": "Prof. Killian (Männlich, Natürlich)"},
    {"id": "de-DE-ConradNeural", "label": "Prof. Conrad (Männlich, Sachlich)"},
    {"id": "de-DE-KatjaNeural", "label": "Prof. Katja (Weiblich, Klar)"},
    {"id": "de-DE-AmalaNeural", "label": "Prof. Amala (Weiblich, Freundlich)"},
]

client = OpenAI(api_key=API_KEY or "unconfigured", base_url=BASE_URL, timeout=45.0)

recognizer = sr.Recognizer()
recognizer.pause_threshold = 3.0
recognizer.non_speaking_duration = 2.0


# ---------------------------------------------------------------------------
# 2. Question Bank & Exam Mode Selection
# ---------------------------------------------------------------------------
def load_questions() -> list[dict]:
    """Load all exam questions from exam_questions.json."""
    for name in ("exam_questions.json", "protokoll_com.json"):
        path = BASE_DIR / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"exam_questions.json not found in {BASE_DIR}.")


def group_questions_by_topic(fragen: list[dict]) -> dict[str, list[dict]]:
    """Group questions into a dictionary keyed by topic name."""
    topics: dict[str, list[dict]] = {}
    for q in fragen:
        topics.setdefault(q.get("thema", "Allgemein"), []).append(q)
    return topics


def select_questions_by_mode(
    fragen: list[dict],
    mode: str = "realistic",
    count: int = 5,
    topic: str | None = None,
) -> tuple[list[dict], str]:
    """Select questions according to the chosen exam mode (shared by Web UI and CLI)."""
    topics = group_questions_by_topic(fragen)
    count = max(1, min(count, len(fragen)))
    mode_key = str(mode).strip().lower()

    if mode_key in ("1", "realistic", "simulation"):
        shuffled_topics = list(topics.keys())
        random.shuffle(shuffled_topics)
        selected: list[dict] = []
        idx = 0
        while len(selected) < count and any(topics.values()):
            pool = topics[shuffled_topics[idx % len(shuffled_topics)]]
            if pool:
                pick = random.choice(pool)
                selected.append(pick)
                pool.remove(pick)
            idx += 1
        return selected, f"Realistische Prüfung ({len(selected)} Fragen aus verschiedenen Themen)"

    if mode_key in ("2", "topic", "thema"):
        topic_names = sorted(topics.keys())
        chosen_topic = topic if (topic and topic in topics) else topic_names[0]
        pool = topics[chosen_topic]
        actual_count = max(1, min(count, len(pool)))
        return random.sample(pool, actual_count), f"Themen-Training: {chosen_topic} ({actual_count} Fragen)"

    if mode_key in ("3", "quick", "schnelldurchlauf"):
        return fragen[:count], f"Schnelldurchlauf (Erste {count} Fragen)"

    raise ValueError(f"Unbekannter Prüfungsmodus: {mode}")


# ---------------------------------------------------------------------------
# 3. LLM Evaluation & German University Grading Scale
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "Du bist ein fairer, fachkundiger Universitätsprofessor in einer mündlichen Prüfung "
    "im Fach 'Simulation und Optimierung' (Informatik).\n\n"
    "WICHTIGE BEWERTUNGSREGELN:\n"
    "1. Die Musterlösung ist nur eine Liste der KERNKONZEPTE, keine Formulierungsvorgabe. "
    "Bewerte den INHALT, nicht den Wortlaut.\n"
    "2. Ein Kernkonzept gilt als abgedeckt ('ja'), wenn der Student es sinngemäß erklärt – "
    "Synonyme, Umschreibungen und eigene Worte sind voll gültig "
    "(z. B. 'in Beziehung stehende Teile' = 'interagierende Teile', "
    "'abstrahierte Darstellung' = 'abstrahiertes Ersatzsystem').\n"
    "3. Zusätzliche, fachlich korrekte Details, Beispiele oder Erweiterungen sind POSITIV "
    "und dürfen NIEMALS zu Punktabzug führen.\n"
    "4. Mündliche Antworten sind oft umgangssprachlich oder unstrukturiert. "
    "Ziehe KEINE Punkte für Stil, Struktur oder Formulierung ab.\n"
    "5. Punktabzug nur für: fehlende Kernkonzepte oder fachlich FALSCHE Aussagen.\n"
    "6. Erfinde keine Anforderungen, die nicht in der Musterlösung stehen.\n\n"
    "VORGEHEN:\n"
    "a) Zerlege die Musterlösung in 2-6 Kernkonzepte.\n"
    "b) Prüfe für jedes Konzept, ob der Student es inhaltlich abdeckt: 'ja', 'teilweise' oder 'nein'.\n"
    "c) Liste nur echte fachliche Fehler auf (keine Stilfragen).\n\n"
    "Antworte IMMER ausschließlich mit folgendem JSON (kein weiterer Text):\n"
    "{\n"
    '  "kernkonzepte": [{"konzept": "<kurz>", "abgedeckt": "ja|teilweise|nein"}],\n'
    '  "fachliche_fehler": ["<nur echte inhaltliche Fehler, sonst leere Liste>"],\n'
    '  "gesprochenes_feedback": "<2-3 Sätze, direkte Anrede. Nenne zuerst, was gut war. '
    'Wenn alle Kernkonzepte abgedeckt sind, lobe klar.>",\n'
    '  "was_fehlt": "<Nur tatsächlich fehlende Kernkonzepte. Wenn nichts fehlt: \'Nichts Wesentliches.\'>",\n'
    '  "merksatz": "<Eine einprägsame Kernaussage für die Wiederholung>"\n'
    "}"
)

COVERAGE_WEIGHTS = {"ja": 1.0, "teilweise": 0.5, "nein": 0.0}


def _score_from_rubric(data: dict) -> int | None:
    """Compute a 0-10 score from concept coverage minus factual-error deductions."""
    concepts = data.get("kernkonzepte") or []
    if not isinstance(concepts, list) or not concepts:
        return None

    weights = [
        COVERAGE_WEIGHTS.get(str(c.get("abgedeckt", "nein")).strip().lower(), 0.0)
        for c in concepts if isinstance(c, dict)
    ]
    if not weights:
        return None

    coverage = sum(weights) / len(weights)
    errors = [e for e in (data.get("fachliche_fehler") or []) if str(e).strip()]
    score = coverage * 10 - 1.5 * len(errors)
    return max(0, min(10, round(score)))


def _status_from_points(pts: int) -> str:
    if pts >= 7:
        return "Bestanden"
    if pts >= 5:
        return "Teilweise"
    return "Nicht bestanden"


def bewerten(
    frage: str,
    musterloesung: str,
    user_antwort: str,
    max_retries: int = 2,
    model_name: str | None = None,
) -> dict:
    """Evaluate a student response against the reference solution using the configured LLM."""
    if not API_KEY:
        return {
            "punkte": 0,
            "status": "Fehler",
            "gesprochenes_feedback": "Es ist kein API-Key konfiguriert. Bitte prüfen Sie Ihre .env-Datei.",
            "was_fehlt": "Technischer Fehler: API_KEY fehlt in .env.",
            "merksatz": "",
        }

    active_model = (model_name or MODEL_NAME or "OpenAI GPT OSS 120B").strip()
    user_prompt = (
        f"Frage: {frage}\n"
        f"Musterlösung: {musterloesung}\n"
        f"Antwort des Studenten: {user_antwort}"
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            # Minimize reasoning overhead on Attempt 0 for fast (~1s) interactive grading
            is_qwen3 = "qwen3" in active_model.lower()
            prompt_suffix = " /no_think" if is_qwen3 and attempt == 0 else ""
            extra_body = (
                {"reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False}}
                if attempt == 0
                else None
            )

            response = client.chat.completions.create(
                model=active_model,
                temperature=0.1,
                max_tokens=900,
                extra_body=extra_body,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + prompt_suffix},
                    {"role": "user", "content": user_prompt + prompt_suffix},
                ],
            )
            content = (response.choices[0].message.content or "").strip()
            # Strip any <think>...</think> blocks if present
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                return {
                    "punkte": 5,
                    "status": "Teilweise",
                    "gesprochenes_feedback": content[:200],
                    "was_fehlt": "Automatisches JSON-Parsing nicht möglich.",
                    "merksatz": "",
                }

            data = json.loads(json_match.group(0))
            pts = _score_from_rubric(data)
            if pts is None:
                try:
                    pts = max(0, min(10, int(data.get("punkte", 5))))
                except (ValueError, TypeError):
                    pts = 5

            return {
                "punkte": pts,
                "status": _status_from_points(pts),
                "kernkonzepte": data.get("kernkonzepte") or [],
                "gesprochenes_feedback": str(data.get("gesprochenes_feedback", "Antwort entgegengenommen.")),
                "was_fehlt": str(data.get("was_fehlt", "")),
                "merksatz": str(data.get("merksatz", "")),
            }
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(1.5)

    return {
        "punkte": 0,
        "status": "Fehler",
        "gesprochenes_feedback": "Bei der Bewertung ist ein Verbindungsfehler aufgetreten. Diese Frage fließt nicht negativ in Ihre Note ein.",
        "was_fehlt": f"Technischer Fehler: {last_error}",
        "merksatz": "",
    }


def build_skipped_evaluation() -> dict:
    """Return a standard 0-point evaluation when a question is skipped."""
    return {
        "punkte": 0,
        "status": "Nicht beantwortet",
        "gesprochenes_feedback": "Da keine Antwort vorliegt, bewerte ich diese Frage mit 0 Punkten und gehe zur nächsten über.",
        "was_fehlt": "Keine Antwort gegeben.",
        "merksatz": "Mut zur Lücke, aber in der Prüfung immer versuchen, zumindest Grundbegriffe zu nennen.",
    }


GRADE_SCALE = [
    (95, 1.0, "Sehr gut (1,0)"),
    (90, 1.3, "Sehr gut (1,3)"),
    (85, 1.7, "Gut (1,7)"),
    (80, 2.0, "Gut (2,0)"),
    (75, 2.3, "Gut (2,3)"),
    (70, 2.7, "Befriedigend (2,7)"),
    (65, 3.0, "Befriedigend (3,0)"),
    (60, 3.3, "Befriedigend (3,3)"),
    (55, 3.7, "Ausreichend (3,7)"),
    (50, 4.0, "Ausreichend (4,0)"),
]


def calculate_grade(percentage: float) -> tuple[float, str]:
    """Calculate the German university grade (1.0 to 5.0) based on percentage."""
    for threshold, grade, label in GRADE_SCALE:
        if percentage >= threshold:
            return grade, label
    return 5.0, "Nicht bestanden (5,0)"


def compute_exam_metrics(results: list[dict]) -> tuple[int, int, float, float, str]:
    """Calculate total score, max score (excluding API errors), percentage, and final grade."""
    valid = [r for r in results if r.get("status") != "Fehler"]
    total_points = sum(int(r.get("punkte", 0)) for r in valid)
    max_points = len(valid) * 10
    percentage = (total_points / max_points * 100) if max_points > 0 else 0.0
    grade, grade_label = calculate_grade(percentage)
    return total_points, max_points, percentage, grade, grade_label


def compute_topic_analytics(results: list[dict]) -> dict:
    """Compute per-topic average scores, strengths (>= 7.5/10), and weaknesses (<= 5.0/10)."""
    topic_scores: dict[str, list[int]] = {}
    for r in results:
        if r.get("status") != "Fehler":
            topic_scores.setdefault(r.get("thema", "Allgemein"), []).append(int(r.get("punkte", 0)))

    strengths, weaknesses, breakdown = [], [], []
    for t_name, scores in sorted(topic_scores.items()):
        avg = sum(scores) / len(scores)
        label = f"{t_name} ({avg:.1f}/10)"
        category = "solid"
        if avg >= 7.5:
            strengths.append(label)
            category = "strength"
        elif avg <= 5.0:
            weaknesses.append(label)
            category = "weakness"

        breakdown.append({
            "thema": t_name,
            "average": round(avg, 2),
            "count": len(scores),
            "total_points": sum(scores),
            "max_points": len(scores) * 10,
            "category": category,
            "label": label,
        })

    return {"strengths": strengths, "weaknesses": weaknesses, "topics": breakdown}


def get_closing_remarks(grade: float, grade_label: str) -> str:
    """Return spoken examiner closing remarks based on the final German grade."""
    if grade <= 2.3:
        return f"Herzlichen Glückwunsch! Sie haben die Prüfung mit der hervorragenden Note {grade_label} bestanden. Sehr gute Leistung!"
    if grade <= 4.0:
        return f"Sie haben die Prüfung mit der Note {grade_label} bestanden. Es gibt noch ein paar Lücken, aber die Grundlagen sitzen."
    return f"Leider hat es heute mit der Note {grade_label} nicht gereicht. Schauen Sie sich das Prüfungsprotokoll an und wiederholen Sie die Schwachstellen."


# ---------------------------------------------------------------------------
# 4. Markdown Exam Transcript Generator
# ---------------------------------------------------------------------------
def generate_exam_report(results: list[dict], mode_name: str) -> Path:
    """Generate a detailed Markdown exam transcript in the 'protokolle/' folder."""
    now = datetime.datetime.now()
    total_points, max_points, percentage, grade, grade_label = compute_exam_metrics(results)
    analytics = compute_topic_analytics(results)

    PROTOKOLLE_DIR.mkdir(parents=True, exist_ok=True)
    report_path = PROTOKOLLE_DIR / f"pruefungsprotokoll_{now.strftime('%Y%m%d_%H%M%S')}.md"

    strengths_str = ", ".join(analytics["strengths"]) or "Keine eindeutigen Schwerpunkte"
    weaknesses_str = ", ".join(analytics["weaknesses"]) or "Keine gravierenden Schwächen"

    md_lines = [
        f"# Mündliches Prüfungsprotokoll: {grade_label}",
        f"**Datum & Uhrzeit:** {now.strftime('%d.%m.%Y %H:%M:%S')}  ",
        f"**Prüfungsmodus:** {mode_name}  ",
        f"**Gesamtergebnis:** {total_points} von {max_points} Punkten ({percentage:.1f}%)  ",
        f"**Endnote:** **{grade_label}**  ",
        "",
        "---",
        "## Zusammenfassung & Feedback",
        f"- **Status:** {'Bestanden' if grade <= 4.0 else 'Nicht bestanden'}",
        f"- **Besondere Stärken:** {strengths_str}",
        f"- **Bereiche mit Nachholbedarf:** {weaknesses_str}",
        "",
        "---",
        "## Detaillierter Fragenverlauf",
        "",
    ]

    for idx, r in enumerate(results, 1):
        md_lines.extend([
            f"### Frage {idx}: [{r['thema']}] (ID: {r['id']})",
            f"**Frage:** {r['frage']}  ",
            f"**Ihre Antwort:** {r.get('user_antwort') or '*(Keine Antwort gegeben)*'}  ",
            f"**Musterlösung:** {r['musterloesung']}  ",
            f"**Bewertung:** **{r['punkte']} / 10 Punkte** ({r['status']})  ",
            f"**Prüfer-Feedback:** {r['gesprochenes_feedback']}  ",
            f"**Fehlende Aspekte:** {r.get('was_fehlt') or 'Keine'}  ",
            f"**Merksatz:** *{r.get('merksatz', '')}*  ",
            "",
            "---",
        ])

    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# 5. Terminal CLI Interface (Fallback / Standalone Usage)
# ---------------------------------------------------------------------------
_pyttsx_engine = None


def _get_pyttsx_engine():
    """Lazy-initialize pyttsx3 for local CLI TTS fallback."""
    global _pyttsx_engine
    if _pyttsx_engine is not None:
        return _pyttsx_engine
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 170)
        for v in engine.getProperty("voices"):
            if any(lang in v.languages for lang in ("de", "de-DE", "de_DE")) or "german" in v.name.lower():
                engine.setProperty("voice", v.id)
                break
        _pyttsx_engine = engine
    except Exception:
        _pyttsx_engine = None
    return _pyttsx_engine


async def _speak_edge(text: str, voice: str = VOICE_NAME):
    """Generate German speech via edge-tts and play it in the CLI using pygame."""
    temp_file = BASE_DIR / "temp_voice.mp3"
    await edge_tts.Communicate(text, voice).save(str(temp_file))
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(temp_file))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
    finally:
        for fn in (pygame.mixer.music.unload, pygame.mixer.quit, temp_file.unlink):
            try:
                fn()
            except Exception:
                pass


def speak(text: str, mute: bool = False):
    """Print examiner statements in the terminal and speak them aloud."""
    print(f"\nPrüfer: {text}")
    if mute or not text.strip():
        return
    if EDGE_TTS_AVAILABLE:
        try:
            asyncio.run(_speak_edge(text))
            return
        except Exception:
            pass
    engine = _get_pyttsx_engine()
    if engine:
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass


def listen_microphone() -> str | None:
    """Capture speech via microphone and transcribe using Google STT."""
    try:
        with sr.Microphone() as source:
            print("\n[Mikrofon aktiv] Bitte sprechen (Denkpausen bis zu 3 Sekunden erlaubt)...")
            recognizer.adjust_for_ambient_noise(source, duration=0.8)
            audio = recognizer.listen(source, timeout=30, phrase_time_limit=180)
            text = recognizer.recognize_google(audio, language="de-DE")
            print(f"Sie (gesprochen): {text}")
            if len(text.split()) <= 3:
                extra = input("Antwort sehr kurz. Ergänzen? [Enter = Absenden / Text tippen]: ").strip()
                if extra:
                    text = f"{text} {extra}"
            return text
    except Exception as exc:
        print(f"[Hinweis] Spracheingabe nicht erkannt ({exc}).")
        return None


def select_questions(fragen: list[dict]) -> tuple[list[dict], str]:
    """Interactive CLI menu for selecting the exam mode."""
    topics = group_questions_by_topic(fragen)
    print("\n" + "=" * 65)
    print("AI-ExamSim: Mündliche Prüfungssimulation (CLI)")
    print("=" * 65)
    print("  [1] Realistische Prüfungssimulation\n  [2] Themen-Training\n  [3] Schnelldurchlauf\n  [4] Beenden")
    choice = input("Modus wählen [1-4, Standard: 1]: ").strip() or "1"

    if choice == "1":
        raw = input("Anzahl Fragen [Standard: 5]: ").strip()
        return select_questions_by_mode(fragen, "realistic", int(raw) if raw.isdigit() else 5)
    if choice == "2":
        names = sorted(topics.keys())
        for i, name in enumerate(names, 1):
            print(f"  [{i:2d}] {name:<30} ({len(topics[name])} Fragen)")
        idx_raw = input("Themen-Nummer [Standard: 1]: ").strip() or "1"
        topic = names[int(idx_raw) - 1] if idx_raw.isdigit() and 1 <= int(idx_raw) <= len(names) else names[0]
        cnt_raw = input(f"Anzahl Fragen aus '{topic}' [Standard: 3]: ").strip()
        return select_questions_by_mode(fragen, "topic", int(cnt_raw) if cnt_raw.isdigit() else 3, topic)
    if choice == "3":
        raw = input("Anzahl Fragen [Standard: 3]: ").strip()
        return select_questions_by_mode(fragen, "quick", int(raw) if raw.isdigit() else 3)
    sys.exit(0)


def main():
    """Run the interactive terminal exam loop."""
    if not API_KEY:
        print("[Fehler] Kein API_KEY in .env gefunden.")
        sys.exit(1)

    fragen = load_questions()
    selected_questions, mode_name = select_questions(fragen)
    speak("Guten Tag! Willkommen zu Ihrer mündlichen Prüfung im Fach Simulation und Optimierung.")

    exam_results = []
    try:
        for idx, item in enumerate(selected_questions, 1):
            print(f"\n[{idx}/{len(selected_questions)}] Thema: {item.get('thema', 'Allgemein')}")
            speak(f"Frage Nummer {idx}: {item['frage']}")
            typed = input("\nAntwort tippen [oder Enter für Mikrofon]: ").strip()
            user_antwort = typed or listen_microphone() or input("Antwort tippen [Enter zum Überspringen]: ").strip()

            if not user_antwort:
                eval_data = build_skipped_evaluation()
                speak(eval_data["gesprochenes_feedback"])
            else:
                eval_data = bewerten(item["frage"], item["musterloesung"], user_antwort)
                speak(eval_data["gesprochenes_feedback"])

            exam_results.append({**item, "user_antwort": user_antwort, **eval_data})
    except KeyboardInterrupt:
        if not exam_results:
            return

    report_path = generate_exam_report(exam_results, mode_name)
    total_pts, max_pts, pct, grade, grade_label = compute_exam_metrics(exam_results)
    print(f"\nErgebnis: {total_pts}/{max_pts} Pkt ({pct:.1f}%) -> {grade_label}")
    print(f"Protokoll: protokolle/{report_path.name}")
    speak(get_closing_remarks(grade, grade_label))


if __name__ == "__main__":
    main()
