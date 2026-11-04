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

# Optional TTS Backends: edge-tts (bevorzugt) oder pyttsx3 (lokaler Fallback)
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
    # Nach einer deutschen Stimme suchen falls vorhanden
    for v in pyttsx_engine.getProperty('voices'):
        if any(lang in v.languages for lang in ['de', 'de-DE', 'de_DE']) or 'german' in v.name.lower() or 'hedda' in v.name.lower() or 'katja' in v.name.lower():
            pyttsx_engine.setProperty('voice', v.id)
            break
except Exception:
    pyttsx_engine = None

# 1. API-Konfiguration laden
load_dotenv()
API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://chat.kiconnect.nrw/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3.8-27b")

if not API_KEY:
    print("Kein API_KEY in .env")

client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)

recognizer = sr.Recognizer()

# ---------------------------------------------------------------------------
# Sprachausgabe (TTS)
# ---------------------------------------------------------------------------
async def _speak_edge(text: str, voice: str = "de-DE-ConradNeural"):
    """Erzeugt natürliche deutsche Sprache via edge-tts und spielt sie mit pygame ab."""
    communicate = edge_tts.Communicate(text, voice)
    temp_file = Path(__file__).resolve().parent / "temp_voice.mp3"
    await communicate.save(str(temp_file))

    pygame.mixer.init()
    pygame.mixer.music.load(str(temp_file))
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)
    pygame.mixer.quit()

    if temp_file.exists():
        try:
            temp_file.unlink()
        except Exception:
            pass

def speak(text: str, mute: bool = False):
    """Gibt Prüfer-Aussagen im Terminal aus und spricht sie optional vor."""
    print(f"\n🎓 Prüfer: {text}")
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
# Spracheingabe (STT) & Tastatur-Fallback
# ---------------------------------------------------------------------------
def listen_microphone() -> str | None:
    """Nimmt Sprache über das Mikrofon auf und transkribiert sie via Google STT."""
    try:
        with sr.Microphone() as source:
            print("\n🎙️ [Mikrofon aktiv] Bitte sprechen (max. 30 Sekunden Aufnahme)...")
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
            try:
                audio = recognizer.listen(source, timeout=8, phrase_time_limit=30)
            except sr.WaitTimeoutError:
                print("⏱️ Keine Spracheingabe erkannt (Zeitüberschreitung).")
                return None

            try:
                text = recognizer.recognize_google(audio, language="de-DE")
                print(f"🗣️ Sie: {text}")
                return text
            except sr.UnknownValueError:
                print("⚠️ Das Gesprochene konnte nicht verstanden werden.")
                return None
            except sr.RequestError as e:
                print(f"⚠️ Netzwerkfehler bei Spracherkennung: {e}")
                return None
    except Exception as e:
        print(f"⚠️ Mikrofonfehler: {e}")
        return None

def get_user_answer() -> str | None:
    """
    Ermöglicht Tastatureingabe oder Umschalten auf Spracheingabe.
    Drückt der Nutzer einfach Enter, startet die Sprachaufnahme.
    """
    print("\n" + "-" * 60)
    print("👉 Ihre Antwort eingeben [Tippen und Enter druecken]")
    print("   ODER einfach [Enter] druecken, um per Mikrofon zu sprechen:")
    typed = input("Antwort: ").strip()

    if typed:
        print(f"⌨️ Sie (getippt): {typed}")
        return typed

    # Enter gedrückt -> Mikrofon starten
    spoken = listen_microphone()
    if not spoken:
        retry_typed = input("Möchten Sie die Antwort stattdessen tippen? [Antwort eingeben / Enter zum Überspringen]: ").strip()
        return retry_typed if retry_typed else None
    return spoken

# ---------------------------------------------------------------------------
# LLM-Bewertung & Notenskalen
# ---------------------------------------------------------------------------
def bewerten(frage: str, musterloesung: str, user_antwort: str) -> dict:
    """
    Sendet Frage, Musterlösung und Studentenantwort an das LLM.
    Erwartet ein strukturiertes JSON mit Punkten (0-10) und Feedback.
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

        # JSON aus Markdown-Blöcken extrahieren falls nötig
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return {
                "punkte": int(data.get("punkte", 5)),
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
        print(f"⚠️ Fehler bei der API-Bewertung: {e}")
        return {
            "punkte": 0,
            "status": "Fehler",
            "gesprochenes_feedback": "Bei der Bewertung ist ein Verbindungsfehler aufgetreten.",
            "was_fehlt": str(e),
            "merksatz": ""
        }

def calculate_grade(percentage: float) -> tuple[float, str]:
    """Berechnet die deutsche Hochschulnote (1.0 bis 5.0) anhand der Prozentpunkte."""
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

# ---------------------------------------------------------------------------
# Fragen laden & Modusauswahl
# ---------------------------------------------------------------------------
def load_questions() -> list[dict]:
    """Lädt die Fragen aus exam_questions.json mit absolutem Pfad."""
    base_dir = Path(__file__).resolve().parent
    file_path = base_dir / "exam_questions.json"
    if not file_path.exists():
        # Fallback auf protokoll_com.json falls noch vorhanden
        alt_path = base_dir / "protokoll_com.json"
        if alt_path.exists():
            file_path = alt_path
        else:
            raise FileNotFoundError(f"Weder exam_questions.json noch protokoll_com.json in {base_dir} gefunden.")

    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def select_questions(fragen: list[dict]) -> tuple[list[dict], str]:
    """Interaktives Menü zur Auswahl des Prüfungsmodus."""
    # Themen gruppieren
    topics: dict[str, list[dict]] = {}
    for q in fragen:
        t = q.get("thema", "Allgemein")
        topics.setdefault(t, []).append(q)

    print("\n" + "=" * 65)
    print("🎓 KI-ExamSim: Mündliche Prüfungssimulation (Simulation & Optimierung)")
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

        # Versuche Fragen aus verschiedenen Themen gleichmäßig zu ziehen
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
# Prüfungsbericht (Report & Transcript)
# ---------------------------------------------------------------------------
def generate_exam_report(results: list[dict], mode_name: str) -> Path:
    """Erstellt ein detailliertes Markdown-Prüfungsprotokoll und speichert es ab."""
    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    date_display = now.strftime("%d.%m.%Y %H:%M:%S")

    total_points = sum(r["punkte"] for r in results)
    max_points = len(results) * 10
    percentage = (total_points / max_points * 100) if max_points > 0 else 0
    grade, grade_label = calculate_grade(percentage)

    # Themenanalyse
    topic_scores: dict[str, list[int]] = {}
    for r in results:
        topic_scores.setdefault(r["thema"], []).append(r["punkte"])

    strengths = []
    weaknesses = []
    for t_name, scores in topic_scores.items():
        avg = sum(scores) / len(scores)
        if avg >= 7.5:
            strengths.append(f"{t_name} ({avg:.1f}/10)")
        elif avg <= 5.0:
            weaknesses.append(f"{t_name} ({avg:.1f}/10)")

    # Report schreiben
    report_filename = f"pruefungsprotokoll_{timestamp_str}.md"
    report_path = Path(__file__).resolve().parent / report_filename

    md_lines = [
        f"# 📋 Mündliches Prüfungsprotokoll: {grade_label}",
        f"**Datum & Uhrzeit:** {date_display}  ",
        f"**Prüfungsmodus:** {mode_name}  ",
        f"**Gesamtergebnis:** {total_points} von {max_points} Punkten ({percentage:.1f}%)  ",
        f"**Endnote:** **{grade_label}**  ",
        "",
        "---",
        "## 📊 Zusammenfassung & Feedback",
        f"- **Status:** {'✅ Bestanden' if grade <= 4.0 else '❌ Nicht bestanden'}",
        f"- **Besondere Stärken:** {', '.join(strengths) if strengths else 'Keine eindeutigen Schwerpunkte'}",
        f"- **Bereiche mit Nachholbedarf:** {', '.join(weaknesses) if weaknesses else 'Keine gravierenden Schwächen'}",
        "",
        "---",
        "## 📝 Detaillierter Fragenverlauf",
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
    """Gibt eine übersichtliche Ergebniskarte im Terminal aus."""
    total_points = sum(r["punkte"] for r in results)
    max_points = len(results) * 10
    percentage = (total_points / max_points * 100) if max_points > 0 else 0
    grade, grade_label = calculate_grade(percentage)

    print("\n" + "=" * 65)
    print("               🏁 PRÜFUNGSERGEBNIS - ZUSAMMENFASSUNG               ")
    print("=" * 65)
    print(f" Modus:           {mode_name}")
    print(f" Geprüfte Fragen: {len(results)}")
    print(f" Gesamtpunkte:    {total_points} / {max_points} ({percentage:.1f}%)")
    print(f" Endnote:         {grade_label}")
    print(f" Status:          {'✅ BESTANDEN' if grade <= 4.0 else '❌ NICHT BESTANDEN'}")
    print("-" * 65)
    print(" Detaillierte Punkteübersicht:")
    for idx, r in enumerate(results, 1):
        status_icon = "🟢" if r["punkte"] >= 7 else ("🟡" if r["punkte"] >= 5 else "🔴")
        print(f"   Frage {idx:02d} [{r['thema']:<25}]: {r['punkte']:2d}/10 Pkt {status_icon}")
    print("-" * 65)
    print(f" 📄 Ausführliches Protokoll gespeichert:")
    print(f"    {report_path.name}")
    print("=" * 65 + "\n")

# ---------------------------------------------------------------------------
# Hauptprogramm (Prüfungs-Loop)
# ---------------------------------------------------------------------------
def main():
    try:
        fragen = load_questions()
    except Exception as e:
        print(f"❌ Fehler beim Laden der Fragen: {e}")
        return

    # Prüfungsmodus auswählen
    selected_questions, mode_name = select_questions(fragen)

    welcome_msg = (
        "Guten Tag! Willkommen zu Ihrer mündlichen Prüfung im Fach Simulation und Optimierung. "
        "Ich werde Ihnen nun nacheinander die Fragen stellen. Sie können per Mikrofon antworten oder die Antwort eintippen. "
        "Lassen Sie uns beginnen."
    )
    speak(welcome_msg)
    time.sleep(0.5)

    exam_results = []

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

        print("\n⏳ Der Prüfer überlegt und bewertet Ihre Antwort...")
        evaluation = bewerten(item["frage"], item["musterloesung"], user_antwort)

        # Prüfer-Feedback vorlesen
        feedback_speech = evaluation.get("gesprochenes_feedback", "Danke für Ihre Ausführung.")
        speak(feedback_speech)

        # Ergebnis erfassen
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

    # Abschlussbericht & Benotung
    report_path = generate_exam_report(exam_results, mode_name)
    print_summary_card(exam_results, mode_name, report_path)

    # Abschlussworte des Prüfers
    total_pts = sum(r["punkte"] for r in exam_results)
    max_pts = len(exam_results) * 10
    pct = (total_pts / max_pts * 100) if max_pts > 0 else 0
    grade, grade_label = calculate_grade(pct)

    if grade <= 2.3:
        closing = f"Herzlichen Glückwunsch! Sie haben die Prüfung mit der hervorragenden Note {grade_label} bestanden. Sehr gute Leistung!"
    elif grade <= 4.0:
        closing = f"Sie haben die Prüfung mit der Note {grade_label} bestanden. Es gibt noch ein paar Lücken, aber die Grundlagen sitzen."
    else:
        closing = f"Leider hat es heute mit der Note {grade_label} nicht gereicht. Schauen Sie sich das Prüfungsprotokoll an und wiederholen Sie die Schwachstellen."

    speak(closing)

if __name__ == "__main__":
    main()
