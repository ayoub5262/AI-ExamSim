# AI-ExamSim: Mündliche Prüfungssimulation

**AI-ExamSim** ist ein interaktiver, sprach- und textgesteuerter Prüfungssimulator für Studierende zur Vorbereitung auf mündliche Hochschulprüfungen im Fach **Simulation und Optimierung** (Informatik / Operations Research).

Das System simuliert eine realistische Prüfungssituation mit einem Universitätsprofessor: Es stellt Fragen, hört auf die gesprochenen oder getippten Antworten des Studierenden, bewertet diese anhand von Musterlösungen mit einem modernen Sprachmodell (LLM) und vergibt eine offizielle Hochschulnote (1,0 bis 5,0) inklusive ausführlichem Prüfungsprotokoll.

---

## Hauptfunktionen (Features)

1. **Hybride Sprache- und Tastatureingabe (Keyboard Fallback):**
   * Antwort direkt über die Tastatur eingeben oder einfach `[Enter]` drücken, um die Spracheingabe per Mikrofon zu starten.
   * Timeout-geschützt (max. 30 Sekunden pro Antwort) mit automatischem Fallback bei Mikrofonproblemen oder komplexen Fachbegriffen.

2. **Hochwertige Sprachausgabe (Neural TTS):**
   * Verwendet **`edge-tts`** mit der natürlichen deutschen Stimme `de-DE-ConradNeural` für eine authentische Prüfer-Akustik.
   * Automatischer Fallback auf lokales `pyttsx3`, falls keine Internetverbindung für TTS besteht.

3. **Drei Prüfungsmodi:**
   * **[1] Realistische Prüfungssimulation:** Zieht $N$ zufällige Fragen gleichmäßig aus verschiedenen Themengebieten für einen repräsentativen Test.
   * **[2] Themen-Training:** Gezieltes Üben einzelner Themen (z. B. *Ereignisdiskrete Simulation*, *Eingabedatenmodellierung*, *Nichtlineare Optimierung*).
   * **[3] Schnelldurchlauf:** Konsekutives Abfragen der ersten $N$ Fragen.

4. **Strukturierte KI-Bewertung und Feedback:**
   * Anbindung an **KI:Connect NRW** (oder OpenAI) mit Modellen wie `qwen3.8-27b`.
   * Pro Frage werden erfasst:
     * **0 bis 10 Punkte**
     * **Status:** Bestanden / Teilweise / Nicht bestanden
     * **Gesprochenes Prüfer-Feedback:** Prägnante Rückmeldung (max. 2–3 Sätze)
     * **Fehlende Aspekte:** Was fehlte zur vollständigen Musterlösung?
     * **Merksatz:** Wichtigste Kernaussage für die Wiederholung.

5. **Prüfungsabschluss und Benotung:**
   * Berechnung der deutschen Hochschulnote nach universitärem Punkteschlüssel (1,0 bis 5,0).
   * Automatische Erstellung eines Markdown-Prüfungsprotokolls (`pruefungsprotokoll_YYYYMMDD_HHMMSS.md`) mit Stärken, Schwachstellen und vollständigem Fragenverlauf.

---

## Projektstruktur

```
AI-ExamSim/
│
├── prof_agent.py              # Hauptprogramm mit Prüfungs-Loop und Logik
├── exam_questions.json        # Fragenkatalog (100 Fragen mit Themen & Musterlösungen)
├── requirements.txt           # Python-Abhängigkeiten
├── .env                       # API-Schlüssel und Konfiguration (lokal)
├── .gitignore                 # Ausgeschlossene Dateien (venv, env, cache)
└── README.md                  # Projektdokumentation
```

---

## Installation und Einrichtung

### 1. Repository vorbereiten und virtuelle Umgebung aktivieren
```powershell
# Virtuelle Umgebung aktivieren (Windows PowerShell)
.\.venv\Scripts\Activate.ps1
```

### 2. Abhängigkeiten installieren
```powershell
pip install -r requirements.txt
```

### 3. Umgebungsvariablen konfigurieren
```ini
API_KEY=
BASE_URL=
MODEL_NAME=
```

---

## Verwendung

Starten Sie die Prüfungssimulation mit folgendem Befehl:

```powershell
python prof_agent.py
```

### Ablauf der Simulation:
1. **Modus wählen:** Wählen Sie zwischen realistischer Prüfung, Themen-Training oder Schnelldurchlauf.
2. **Frage anhören / lesen:** Der Prüfer stellt die Frage sowohl akustisch als auch im Terminal.
3. **Antworten:**
   * **Tippen:** Geben Sie Ihre Antwort direkt ein und drücken Sie `Enter`.
   * **Sprechen:** Drücken Sie direkt `Enter` mit leerem Textfeld, um das Mikrofon zu aktivieren.
4. **Feedback:** Der Prüfer bewertet Ihre Antwort direkt und nennt Ihnen fehlende Aspekte.
5. **Ergebnis:** Am Ende erhalten Sie Ihre Gesamtnote, eine Stärken- und Schwächen-Analyse sowie ein gespeichertes Prüfungsprotokoll im Markdown-Format.

---

## Notenschlüssel

| Prozent | Note | Bewertung |
| :--- | :--- | :--- |
| $\ge 95\%$ | **1,0** | Sehr gut |
| $\ge 90\%$ | **1,3** | Sehr gut |
| $\ge 85\%$ | **1,7** | Gut |
| $\ge 80\%$ | **2,0** | Gut |
| $\ge 75\%$ | **2,3** | Gut |
| $\ge 70\%$ | **2,7** | Befriedigend |
| $\ge 65\%$ | **3,0** | Befriedigend |
| $\ge 60\%$ | **3,3** | Befriedigend |
| $\ge 55\%$ | **3,7** | Ausreichend |
| $\ge 50\%$ | **4,0** | Ausreichend |
| $< 50\%$ | **5,0** | Nicht bestanden |

---

## Lizenz und Datenschutz

* Konzipiert für akademische Lernzwecke.
* Fragen und Musterlösungen basieren auf Prüfungsprotokollen des Fachs Simulation & Optimierung.
* Bei Verwendung von KI:Connect NRW verbleiben die Daten im geschützten akademischen Netzwerksystem.
