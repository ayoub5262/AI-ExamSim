# AI-ExamSim: Oral Examination Simulator

**AI-ExamSim** is an interactive, voice- and text-driven oral examination simulator for university students preparing for **Model-based Analysis and Optimization** (*Modellgestützte Analyse und Optimierung*).

It provides both a **modern Single-Page Web Application (FastAPI + Tailwind CSS + Vanilla JS)** and a **Terminal CLI**, simulating a university professor who poses questions via neural speech synthesis (`edge-tts`), listens to spoken or typed answers (with a 3-second thinking pause tolerance), evaluates responses against reference solutions via an OpenAI-compatible LLM, and calculates a German university grade (`1.0` to `5.0`) with a downloadable Markdown transcript.

---

## Project Structure

```text
AI-ExamSim/
├── app.py                   # FastAPI server & REST API endpoints
├── prof_agent.py            # Shared core logic (LLM evaluation, grading, analytics, CLI)
├── exam_questions.json      # Question bank (100 questions across 12 topics)
├── templates/
│   └── index.html           # Responsive Single-Page Web UI (Tailwind CSS)
├── static/
│   ├── css/style.css        # Custom styles & waveform animations
│   └── js/app.js            # Frontend state, Web Speech API & audio controller
├── protokolle/              # Generated Markdown examination transcripts
├── requirements.txt         # Python dependencies
└── .env                     # API credentials and model/voice configuration
```

---

## Quick Start

### 1. Install Dependencies

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure `.env`

```ini
API_KEY=your_api_key_here
BASE_URL=https://chat.kiconnect.nrw/api/v1
MODEL_NAME=qwen3.8-27b
VOICE_NAME=de-DE-KillianNeural
```

### 3. Launch the Web UI (Recommended)

```powershell
python app.py
```

Open **http://localhost:8000** in your browser (also compatible with IDE Live Server previews).

### 4. Launch the Terminal CLI (Optional)

```powershell
python prof_agent.py
```

---

## API Overview (`app.py`)

| Method & Endpoint | Description |
| :--- | :--- |
| `GET /` | Serves the Single-Page Web Application (`templates/index.html`) |
| `GET /api/topics` | Lists all 12 topics, question counts, and active model/voice config |
| `POST /api/exam/start` | Starts a new exam session (`realistic`, `topic`, or `quick` mode) |
| `POST /api/tts` | Streams German neural speech (`edge-tts`) in memory as `audio/mpeg` |
| `POST /api/evaluate` | Evaluates a student response (`0–10` points, status, feedback, takeaway) |
| `POST /api/stt` | Optional server-side WAV speech-to-text fallback |
| `POST /api/exam/finish` | Computes final German grade, topic analytics, and saves `.md` protocol |
| `GET /api/protokolle/{filename}` | Downloads a saved Markdown examination transcript |

---

## Grading Scale (German University System)

| Percentage | Grade | Classification |
| :--- | :--- | :--- |
| $\ge 95\%$ | **1.0** | Sehr gut |
| $\ge 90\%$ | **1.3** | Sehr gut |
| $\ge 85\%$ | **1.7** | Gut |
| $\ge 80\%$ | **2.0** | Gut |
| $\ge 75\%$ | **2.3** | Gut |
| $\ge 70\%$ | **2.7** | Befriedigend |
| $\ge 65\%$ | **3.0** | Befriedigend |
| $\ge 60\%$ | **3.3** | Befriedigend |
| $\ge 55\%$ | **3.7** | Ausreichend |
| $\ge 50\%$ | **4.0** | Ausreichend |
| $< 50\%$ | **5.0** | Nicht bestanden |
