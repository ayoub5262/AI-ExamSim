import asyncio
import datetime
import json
import os
import random
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
import speech_recognition as sr

# Suppress Pygame welcome message
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

# Optional TTS backends: edge-tts (preferred) or pyttsx3 (local fallback)
try:
    import edge_tts
    import pygame
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

try:
    import pyttsx3
    pyttsx_engine = pyttsx3.init()
    pyttsx_engine.setProperty('rate', 170)
    # Search for an installed German voice if available
    for v in pyttsx_engine.getProperty('voices'):
        if any(lang in v.languages for lang in ['de', 'de-DE', 'de_DE']) or 'german' in v.name.lower() or 'hedda' in v.name.lower() or 'katja' in v.name.lower():
            pyttsx_engine.setProperty('voice', v.id)
            break
except Exception:
    pyttsx_engine = None

# 1. Load API configuration
load_dotenv()
API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://chat.kiconnect.nrw/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.8-27b")
VOICE_NAME = os.getenv("VOICE_NAME", "de-DE-KillianNeural")

if not API_KEY:
    print("[Fehler] Kein API_KEY in .env gefunden. Bitte konfigurieren Sie Ihren API_KEY in der .env-Datei.")
    sys.exit(1)

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

recognizer = sr.Recognizer()
# Allow thinking pauses while speaking (default library threshold is only 0.8s, which causes premature cutoffs)
recognizer.pause_threshold = 3.0
recognizer.non_speaking_duration = 2.0

# ---------------------------------------------------------------------------
# Text-to-Speech (TTS)
# ---------------------------------------------------------------------------
async def _speak_edge(text: str, voice: str = VOICE_NAME):
    """Generate natural German speech via edge-tts and play it using pygame."""
    communicate = edge_tts.Communicate(text, voice)
    temp_file = Path(__file__).resolve().parent / "temp_voice.mp3"
    await communicate.save(str(temp_file))

    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(temp_file))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
    finally:
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

def speak(text: str, mute: bool = False):
    """Print examiner statements in the terminal and optionally speak them aloud."""
    print(f"\nPrüfer: {text}")
    if mute or not text.strip():
        return

    if EDGE_TTS_AVAILABLE:
        try:
            asyncio.run(_speak_edge(text))
            return
        except Exception:
            pass

    if pyttsx_engine:
        try:
            pyttsx_engine.say(text)
            pyttsx_engine.runAndWait()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Speech-to-Text (STT) & Keyboard Fallback
# ---------------------------------------------------------------------------
def listen_microphone() -> str | None:
    """Capture speech via microphone and transcribe using Google STT."""
    try:
        with sr.Microphone() as source:
            print("\n[Mikrofon aktiv] Bitte sprechen (Denkpausen bis zu 3 Sekunden erlaubt, max. 3 Minuten)...")
            recognizer.adjust_for_ambient_noise(source, duration=0.8)
            try:
                audio = recognizer.listen(source, timeout=30, phrase_time_limit=180)
            except sr.WaitTimeoutError:
                print("[Hinweis] Keine Spracheingabe erkannt (Zeitüberschreitung).")
                return None

            try:
                text = recognizer.recognize_google(audio, language="de-DE")
                print(f"Sie (gesprochen): {text}")

                # Safety net: In case only 1-3 words were captured (e.g. due to an extended thinking pause)
                if len(text.split()) <= 3:
                    print("\n[Hinweis] Ihre Aufnahme war sehr kurz. Haben Sie noch nachgedacht?")
                    extra = input("Möchten Sie etwas ergänzen? [Enter = So absenden / Text tippen zum Ergänzen]: ").strip()
                    if extra:
                        text = f"{text} {extra}"
                        print(f"Vollständige Antwort: {text}")

                return text
            except sr.UnknownValueError:
                print("[Hinweis] Das Gesprochene konnte akustisch nicht verstanden werden.")
                return None
            except sr.RequestError as e:
                print(f"[Warnung] Netzwerkfehler bei Spracherkennung: {e}")
                return None
    except Exception as e:
        print(f"[Warnung] Mikrofonfehler: {e}")
        return None

def get_user_answer() -> str | None:
    """
    Allow keyboard input or switch to microphone input.
    If the user presses Enter with empty input, voice recording starts.
    """
    print("\n" + "-" * 65)
    print("Ihre Antwort eingeben [Tippen und Enter drücken]")
    print("ODER einfach [Enter] drücken, um per Mikrofon zu sprechen:")
    typed = input("Antwort: ").strip()

    if typed:
        print(f"Sie (getippt): {typed}")
        return typed

    # Enter pressed without text -> start microphone
    spoken = listen_microphone()
    if not spoken:
        retry_typed = input("Möchten Sie die Antwort stattdessen tippen? [Antwort eingeben / Enter zum Überspringen]: ").strip()
        return retry_typed if retry_typed else None
    return spoken

# ---------------------------------------------------------------------------
# LLM Evaluation & Grading Scales
# ---------------------------------------------------------------------------
def bewerten(frage: str, musterloesung: str, user_antwort: str, max_retries: int = 2) -> dict:
    """
    Send question, model solution, and student response to the LLM.
    Expects structured JSON with score (0-10) and feedback.
    """
    system_prompt = (
        "Du bist ein erfahrener, strenger, aber wohlwollender Universitätsprofessor "
        "in einer mündlichen Prüfung im Fach 'Simulation und Optimierung' (Informatik).\n"
        "Bewerte die Antwort des Studenten objektiv auf einer Skala von 0 bis 10 Punkten anhand der Musterlösung.\n"
        "0-4 Punkte: Nicht bestanden / unzureichend.\n"
        "5-7 Punkte: Bestanden / solides Grundwissen mit Lücken.\n"
        "8-10 Punkte: Gut bis sehr gut / präzise und vollständig.\n\n"
        "Antworte IMMER im folgenden JSON-Format ohne weiteren Text:\n"
        "{\n"
        '  "punkte": <Zahl von 0 bis 10>,\n'
        '  "status": "<Bestanden | Teilweise | Nicht bestanden>",\n'
        '  "gesprochenes_feedback": "<Kurzes, prägnantes Feedback in max. 2-3 Sätzen. Sprich den Studenten direkt an. Sag klar, ob es richtig war und was der Kernpunkt ist.>",\n'
        '  "was_fehlt": "<Konkrete fehlende Begriffe oder Konzepte in 1-2 Sätzen>",\n'
        '  "merksatz": "<Eine einprägsame Kernaussage für die Wiederholung>"\n'
        "}"
    )

    user_prompt = (
        f"Frage: {frage}\n"
        f"Musterlösung: {musterloesung}\n"
        f"Antwort des Studenten: {user_antwort}"
    )

    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            content = response.choices[0].message.content.strip()

            # Extract JSON from Markdown code blocks if necessary
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                raw_pts = data.get("punkte", 5)
                try:
                    pts = max(0, min(10, int(raw_pts)))
                except (ValueError, TypeError):
                    pts = 5

                return {
                    "punkte": pts,
                    "status": str(data.get("status", "Teilweise")),
                    "gesprochenes_feedback": str(data.get("gesprochenes_feedback", "Antwort entgegengenommen.")),
                    "was_fehlt": str(data.get("was_fehlt", "")),
                    "merksatz": str(data.get("merksatz", ""))
                }
            else:
                return {
                    "punkte": 5,
                    "status": "Teilweise",
                    "gesprochenes_feedback": content[:200],
                    "was_fehlt": "Automatisches Parsing nicht möglich.",
                    "merksatz": ""
                }
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(1.5)

    print(f"[Warnung] Fehler bei der API-Bewertung: {last_error}")
    return {
        "punkte": 0,
        "status": "Fehler",
        "gesprochenes_feedback": "Bei der Bewertung ist ein Verbindungsfehler aufgetreten. Diese Frage fließt nicht negativ in Ihre Note ein.",
        "was_fehlt": f"Technischer Fehler: {last_error}",
        "merksatz": ""
    }

def calculate_grade(percentage: float) -> tuple[float, str]:
    """Calculate the German university grade (1.0 to 5.0) based on percentage."""
    if percentage >= 95:
        return 1.0, "Sehr gut (1,0)"
    elif percentage >= 90:
        return 1.3, "Sehr gut (1,3)"
    elif percentage >= 85:
        return 1.7, "Gut (1,7)"
    elif percentage >= 80:
        return 2.0, "Gut (2,0)"
    elif percentage >= 75:
        return 2.3, "Gut (2,3)"
    elif percentage >= 70:
        return 2.7, "Befriedigend (2,7)"
    elif percentage >= 65:
        return 3.0, "Befriedigend (3,0)"
    elif percentage >= 60:
        return 3.3, "Befriedigend (3,3)"
    elif percentage >= 55:
        return 3.7, "Ausreichend (3,7)"
    elif percentage >= 50:
        return 4.0, "Ausreichend (4,0)"
    else:
        return 5.0, "Nicht bestanden (5,0)"

def compute_exam_metrics(results: list[dict]) -> tuple[int, int, float, float, str]:
    """Calculate total score, max score (excluding API errors), percentage, and final grade."""
    valid_results = [r for r in results if r.get("status") != "Fehler"]
    total_points = sum(r["punkte"] for r in valid_results)
    max_points = len(valid_results) * 10
    percentage = (total_points / max_points * 100) if max_points > 0 else 0.0
    grade, grade_label = calculate_grade(percentage)
    return total_points, max_points, percentage, grade, grade_label

# ---------------------------------------------------------------------------
# Question Loading & Mode Selection
# ---------------------------------------------------------------------------
def load_questions() -> list[dict]:
    """Load questions from exam_questions.json using an absolute path."""
    base_dir = Path(__file__).resolve().parent
    file_path = base_dir / "exam_questions.json"
    if not file_path.exists():
        alt_path = base_dir / "protokoll_com.json"
        if alt_path.exists():
            file_path = alt_path
        else:
            raise FileNotFoundError(f"Neither exam_questions.json nor protokoll_com.json found in {base_dir}.")

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def select_questions(fragen: list[dict]) -> tuple[list[dict], str]:
    """Interactive menu for selecting the exam mode."""
    topics: dict[str, list[dict]] = {}
    for q in fragen:
        t = q.get("thema", "Allgemein")
        topics.setdefault(t, []).append(q)

    print("\n" + "=" * 65)
    print("KI-ExamSim: Mündliche Prüfungssimulation (Modellgestützte Analyse und Optimierung)")
    print("=" * 65)
    print("Verfügbare Modi:")
    print("  [1] Realistische Prüfungssimulation (Zufällige Fragen aus diversen Themen)")
    print("  [2] Themen-Training (Gezielt ein bestimmtes Fachgebiet üben)")
    print("  [3] Schnelldurchlauf (Erste N Fragen der Reihe nach)")
    print("  [4] Beenden")
    print("-" * 65)

    choice = input("Wählen Sie einen Modus [1-4, Standard: 1]: ").strip() or "1"

    if choice == "1":
        default_count = 5
        count_input = input(f"Wie viele Fragen sollen geprüft werden? [Standard: {default_count}]: ").strip()
        count = int(count_input) if count_input.isdigit() and int(count_input) > 0 else default_count

        # Distribute questions evenly across different topics
        shuffled_topics = list(topics.keys())
        random.shuffle(shuffled_topics)
        selected = []
        topic_cycle = 0
        while len(selected) < count and any(topics.values()):
            curr_topic = shuffled_topics[topic_cycle % len(shuffled_topics)]
            if topics[curr_topic]:
                pick = random.choice(topics[curr_topic])
                selected.append(pick)
                topics[curr_topic].remove(pick)
            topic_cycle += 1
            if all(len(t_list) == 0 for t_list in topics.values()):
                break

        return selected, f"Realistische Prüfung ({len(selected)} Fragen aus verschiedenen Themen)"

    elif choice == "2":
        topic_names = sorted(topics.keys())
        print("\nVerfügbare Themen:")
        for idx, t_name in enumerate(topic_names, 1):
            print(f"  [{idx:2d}] {t_name:<30} ({len(topics[t_name])} Fragen)")

        t_idx_input = input("\nThemen-Nummer eingeben [Standard: 1]: ").strip() or "1"
        try:
            chosen_topic = topic_names[int(t_idx_input) - 1]
        except (ValueError, IndexError):
            chosen_topic = topic_names[0]

        q_pool = topics[chosen_topic]
        count_input = input(f"Wie viele Fragen aus '{chosen_topic}'? [1-{len(q_pool)}, Standard: 3]: ").strip()
        count = int(count_input) if count_input.isdigit() and int(count_input) > 0 else 3
        count = min(count, len(q_pool))

        selected = random.sample(q_pool, count)
        return selected, f"Themen-Training: {chosen_topic} ({count} Fragen)"

    elif choice == "3":
        count_input = input("Wie viele Fragen möchten Sie testen? [Standard: 3]: ").strip()
        count = int(count_input) if count_input.isdigit() and int(count_input) > 0 else 3
        return fragen[:count], f"Schnelldurchlauf (Erste {count} Fragen)"

    else:
        print("Auf Wiedersehen!")
        sys.exit(0)

# ---------------------------------------------------------------------------
# Exam Report & Transcript
# ---------------------------------------------------------------------------
def generate_exam_report(results: list[dict], mode_name: str) -> Path:
    """Generate a detailed Markdown exam transcript in the 'protokolle/' folder."""
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    date_display = now.strftime("%d.%m.%Y %H:%M:%S")

    total_points, max_points, percentage, grade, grade_label = compute_exam_metrics(results)

    # Topic breakdown (valid evaluations only)
    topic_scores: dict[str, list[int]] = {}
    for r in results:
        if r.get("status") != "Fehler":
            topic_scores.setdefault(r["thema"], []).append(r["punkte"])

    strengths = []
    weaknesses = []
    for t_name, scores in topic_scores.items():
        avg = sum(scores) / len(scores)
        if avg >= 7.5:
            strengths.append(f"{t_name} ({avg:.1f}/10)")
        elif avg <= 5.0:
            weaknesses.append(f"{t_name} ({avg:.1f}/10)")

    # Ensure 'protokolle' directory exists and write report
    reports_dir = Path(__file__).resolve().parent / "protokolle"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_filename = f"pruefungsprotokoll_{timestamp_str}.md"
    report_path = reports_dir / report_filename

    md_lines = [
        f"# Mündliches Prüfungsprotokoll: {grade_label}",
        f"**Datum & Uhrzeit:** {date_display}  ",
        f"**Prüfungsmodus:** {mode_name}  ",
        f"**Gesamtergebnis:** {total_points} von {max_points} Punkten ({percentage:.1f}%)  ",
        f"**Endnote:** **{grade_label}**  ",
        "",
        "---",
        "## Zusammenfassung & Feedback",
        f"- **Status:** {'Bestanden' if grade <= 4.0 else 'Nicht bestanden'}",
        f"- **Besondere Stärken:** {', '.join(strengths) if strengths else 'Keine eindeutigen Schwerpunkte'}",
        f"- **Bereiche mit Nachholbedarf:** {', '.join(weaknesses) if weaknesses else 'Keine gravierenden Schwächen'}",
        "",
        "---",
        "## Detaillierter Fragenverlauf",
        ""
    ]

    for idx, r in enumerate(results, 1):
        md_lines.extend([
            f"### Frage {idx}: [{r['thema']}] (ID: {r['id']})",
            f"**Frage:** {r['frage']}  ",
            f"**Ihre Antwort:** {r['user_antwort'] or '*(Keine Antwort gegeben)*'}  ",
            f"**Musterlösung:** {r['musterloesung']}  ",
            f"**Bewertung:** **{r['punkte']} / 10 Punkte** ({r['status']})  ",
            f"**Prüfer-Feedback:** {r['gesprochenes_feedback']}  ",
            f"**Fehlende Aspekte:** {r['was_fehlt'] or 'Keine'}  ",
            f"**Merksatz:** *{r['merksatz']}*  ",
            "",
            "---"
        ])

    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    return report_path

def print_summary_card(results: list[dict], mode_name: str, report_path: Path):
    """Print an exam summary card in the terminal."""
    total_points, max_points, percentage, grade, grade_label = compute_exam_metrics(results)

    print("\n" + "=" * 65)
    print("               PRÜFUNGSERGEBNIS - ZUSAMMENFASSUNG                ")
    print("=" * 65)
    print(f" Modus:           {mode_name}")
    print(f" Geprüfte Fragen: {len(results)}")
    print(f" Gesamtpunkte:    {total_points} / {max_points} ({percentage:.1f}%)")
    print(f" Endnote:         {grade_label}")
    print(f" Status:          {'BESTANDEN' if grade <= 4.0 else 'NICHT BESTANDEN'}")
    print("-" * 65)
    print(" Detaillierte Punkteübersicht:")
    for idx, r in enumerate(results, 1):
        if r.get("status") == "Fehler":
            status_tag = "[FEHLER]"
        elif r["punkte"] >= 7:
            status_tag = "[GUT]"
        elif r["punkte"] >= 5:
            status_tag = "[OK]"
        else:
            status_tag = "[LÜCKE]"
        print(f"   Frage {idx:02d} [{r['thema']:<25}]: {r['punkte']:2d}/10 Pkt {status_tag}")
    print("-" * 65)
    print(" Ausführliches Protokoll gespeichert unter:")
    print(f"   protokolle/{report_path.name}")
    print("=" * 65 + "\n")

# ---------------------------------------------------------------------------
# Main Program (Exam Loop)
# ---------------------------------------------------------------------------
def main():
    try:
        fragen = load_questions()
    except Exception as e:
        print(f"[Fehler] Beim Laden der Fragen ist ein Problem aufgetreten: {e}")
        return

    # Select exam mode
    try:
        selected_questions, mode_name = select_questions(fragen)
    except KeyboardInterrupt:
        print("\nPrüfung vorzeitig beendet.")
        return

    welcome_msg = (
        "Guten Tag! Willkommen zu Ihrer mündlichen Prüfung im Fach Simulation und Optimierung. "
        "Ich werde Ihnen nun nacheinander die Fragen stellen. Sie können per Mikrofon antworten oder die Antwort eintippen. "
        "Lassen Sie uns beginnen."
    )
    speak(welcome_msg)
    time.sleep(0.5)

    exam_results = []

    try:
        for idx, item in enumerate(selected_questions, 1):
            print(f"\n[{idx}/{len(selected_questions)}] Thema: {item.get('thema', 'Allgemein')}")
            speak(f"Frage Nummer {idx}: {item['frage']}")
            time.sleep(0.3)

            user_antwort = get_user_answer()

            if not user_antwort or not user_antwort.strip():
                speak("Da keine Antwort vorliegt, bewerte ich diese Frage mit 0 Punkten und gehe zur nächsten über.")
                exam_results.append({
                    "id": item.get("id", idx),
                    "thema": item.get("thema", "Allgemein"),
                    "frage": item["frage"],
                    "musterloesung": item["musterloesung"],
                    "user_antwort": "",
                    "punkte": 0,
                    "status": "Nicht beantwortet",
                    "gesprochenes_feedback": "Keine Antwort gegeben.",
                    "was_fehlt": "Keine Antwort gegeben.",
                    "merksatz": "Mut zur Lücke, aber in der Prüfung immer versuchen etwas Relevantes zu sagen."
                })
                continue

            print("\n[Info] Der Prüfer überlegt und bewertet Ihre Antwort...")
            evaluation = bewerten(item["frage"], item["musterloesung"], user_antwort)

            # Speak examiner feedback
            feedback_speech = evaluation.get("gesprochenes_feedback", "Danke für Ihre Ausführung.")
            speak(feedback_speech)

            # Record result
            exam_results.append({
                "id": item.get("id", idx),
                "thema": item.get("thema", "Allgemein"),
                "frage": item["frage"],
                "musterloesung": item["musterloesung"],
                "user_antwort": user_antwort,
                "punkte": evaluation.get("punkte", 5),
                "status": evaluation.get("status", "Teilweise"),
                "gesprochenes_feedback": feedback_speech,
                "was_fehlt": evaluation.get("was_fehlt", ""),
                "merksatz": evaluation.get("merksatz", "")
            })

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n[Hinweis] Prüfung durch Benutzer vorzeitig abgebrochen.")
        if not exam_results:
            print("Es wurden noch keine Fragen beantwortet. Es wird kein Protokoll erstellt.")
            return
        print("Bisherige Antworten werden ausgewertet und gespeichert...")

    # Final report & grading
    report_path = generate_exam_report(exam_results, mode_name)
    print_summary_card(exam_results, mode_name, report_path)

    # Examiner closing remarks
    _, _, _, grade, grade_label = compute_exam_metrics(exam_results)

    if grade <= 2.3:
        closing = f"Herzlichen Glückwunsch! Sie haben die Prüfung mit der hervorragenden Note {grade_label} bestanden. Sehr gute Leistung!"
    elif grade <= 4.0:
        closing = f"Sie haben die Prüfung mit der Note {grade_label} bestanden. Es gibt noch ein paar Lücken, aber die Grundlagen sitzen."
    else:
        closing = f"Leider hat es heute mit der Note {grade_label} nicht gereicht. Schauen Sie sich das Prüfungsprotokoll an und wiederholen Sie die Schwachstellen."

    speak(closing)

if __name__ == "__main__":
    main()
