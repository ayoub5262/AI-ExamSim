# AI-ExamSim: Oral Examination Simulator

**AI-ExamSim** is an interactive, voice- and text-driven exam simulator designed for university students preparing for oral exams in **Model-based analysis and optimization** (Computer Science).

The system simulates a realistic oral exam with a university professor: it poses questions, listens to spoken or typed student answers, evaluates responses against curated reference solutions using a large language model (LLM), and assigns an official German university grade (1.0 to 5.0) alongside a comprehensive examination transcript.

---

## Core Features

1. **Hybrid Voice and Keyboard Input (Keyboard Fallback):**
   * Type answers directly into the console or press `[Enter]` on an empty prompt to trigger microphone recording.
   * Thinking-pause tolerant (up to 3 seconds of pause allowed mid-sentence) with up to 3 minutes of recording time and automatic fallback to keyboard if microphone issues occur.

2. **High-Quality Neural Speech Synthesis (TTS):**
   * Powered by **`edge-tts`** using natural German neural voices (default: `de-DE-KillianNeural`, configurable via `VOICE_NAME` in `.env`).
   * Automatic fallback to local `pyttsx3` or terminal output when offline.

3. **Three Examination Modes:**
   * **[1] Realistic Exam Simulation:** Samples $N$ random questions balanced evenly across diverse topic areas for a representative exam.
   * **[2] Topic-Specific Training:** Focus on a single topic (e.g., *Discrete Event Simulation*, *Input Data Modeling*, *Nonlinear Optimization*).
   * **[3] Quick Run:** Sequential walk-through of the first $N$ questions.

4. **Structured AI Evaluation and Feedback:**
   * Connects to **KI:Connect NRW** (or any OpenAI-compatible API) using models such as `qwen3.8-27b`.
   * For every question, the evaluator outputs:
     * **Score:** 0 to 10 points
     * **Status:** Passed / Partial / Failed
     * **Spoken Feedback:** Concise remarks addressed directly to the student (max. 2–3 sentences)
     * **Missing Concepts:** Concrete gaps compared to the reference solution
     * **Takeaway Note (Merksatz):** A memorable summary point for review

5. **Official University Grading and Transcripts:**
   * Computes the final German university grade based on the standard university percentage scale (1.0 to 5.0).
   * Automatically generates a detailed Markdown exam transcript (`protokolle/pruefungsprotokoll_YYYYMMDD_HHMMSS.md`) with strengths, weaknesses, and a full question-by-question breakdown.

---

## Project Structure

```
AI-ExamSim/
│
├── prof_agent.py              # Main exam loop and application logic
├── exam_questions.json        # Question bank (100 questions with topics & reference solutions)
├── protokolle/                # Automatically generated exam transcripts (Markdown)
├── requirements.txt           # Python package dependencies
├── .env                       # Local API keys and environment configuration
├── .env.example               # Template for environment variables
├── .gitignore                 # Ignored files (venv, env, cache, protokolle)
└── README.md                  # Project documentation
```

---

## Installation and Setup

### 1. Activate the Virtual Environment
```powershell
# In Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy the template `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Set your credentials in `.env`:
```ini
API_KEY=your_api_key_here
BASE_URL=https://chat.kiconnect.nrw/api/v1
MODEL_NAME=qwen3.8-27b
VOICE_NAME=de-DE-KillianNeural
```

---

## Usage

Start the examination simulator with:

```powershell
python prof_agent.py
```

### Simulation Flow:
1. **Select Mode:** Choose between realistic simulation, topic-specific training, or quick run.
2. **Review Question:** The examiner reads the question aloud and prints it to the terminal.
3. **Submit Answer:**
   * **Typing:** Enter your response directly and press `Enter`.
   * **Speaking:** Press `Enter` on an empty line to start microphone recording.
4. **Immediate Feedback:** The examiner evaluates your response, gives verbal feedback, and notes missing points.
5. **Final Evaluation:** At the end of the session, review your final grade, a breakdown of strengths and weaknesses, and access the generated transcript file in `protokolle/`.

---

## Grading Scale (German University System)

| Percentage | Grade | Classification |
| :--- | :--- | :--- |
| $\ge 95\%$ | **1.0** | Very Good (Sehr gut) |
| $\ge 90\%$ | **1.3** | Very Good (Sehr gut) |
| $\ge 85\%$ | **1.7** | Good (Gut) |
| $\ge 80\%$ | **2.0** | Good (Gut) |
| $\ge 75\%$ | **2.3** | Good (Gut) |
| $\ge 70\%$ | **2.7** | Satisfactory (Befriedigend) |
| $\ge 65\%$ | **3.0** | Satisfactory (Befriedigend) |
| $\ge 60\%$ | **3.3** | Satisfactory (Befriedigend) |
| $\ge 55\%$ | **3.7** | Sufficient (Ausreichend) |
| $\ge 50\%$ | **4.0** | Sufficient (Ausreichend) |
| $< 50\%$ | **5.0** | Failed (Nicht bestanden) |

---

## License and Privacy

* Intended for academic training and learning purposes.
* Question pool and reference solutions are based on exam protocols from Simulation and Optimization courses.
* When using KI:Connect NRW, data processing stays within the secured academic network.
