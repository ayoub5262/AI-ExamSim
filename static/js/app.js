/**
 * AI-ExamSim Frontend Controller
 * Structured into 5 sections:
 *   1. State & API Client (with automatic port-8000 routing for Live Server)
 *   2. UI, Notifications & Examiner Audio (TTS)
 *   3. Microphone Check, Waveform Visualizer & Speech-to-Text (STT)
 *   4. Exam Session Flow & Evaluation
 *   5. Results Dashboard & Event Bindings
 */

(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // -------------------------------------------------------------------------
  // 1. State & API Client
  // -------------------------------------------------------------------------
  let API_BASE = (() => {
    const { protocol, hostname, port } = window.location;
    if (protocol === 'file:') return 'http://127.0.0.1:8000';
    if (port && port !== '8000') return `${protocol}//${hostname || '127.0.0.1'}:8000`;
    return '';
  })();

  const apiUrl = (path) => {
    if (!path || /^https?:\/\//i.test(path)) return path || '';
    return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
  };

  async function apiFetch(path, options = {}) {
    let res = await fetch(apiUrl(path), options);
    if ((res.status === 405 || res.status === 404) && API_BASE === '') {
      API_BASE = `http://${window.location.hostname || '127.0.0.1'}:8000`;
      res = await fetch(apiUrl(path), options);
    }
    return res;
  }

  const state = {
    topics: [],
    voiceName: 'de-DE-KillianNeural',
    autoPlayTts: true,

    // Setup configuration
    selectedMode: 'realistic',
    counts: { realistic: 5, topic: 3, quick: 3 },
    selectedTopic: '',

    // Active Exam Session
    sessionId: null,
    modeName: '',
    questions: [],
    currentIndex: 0,
    results: [],
    isEvaluating: false,
    shortAnswerConfirmed: false,

    // Audio & Speech
    currentAudio: null,
    currentAudioUrl: null,
    ttsAbort: null,
    speechCache: { question: '', feedback: '', closing: '' },

    // Recording & STT
    isRecording: false,
    isPaused: false,
    recordingSeconds: 0,
    recordingInterval: null,
    recognition: null,
    committedText: '',
    pauseTimeout: null,
    mediaStream: null,
    audioCtx: null,
    animFrame: null,
    wavRecorder: null,
    wavChunks: [],
    micTestCleanup: null,
  };

  // -------------------------------------------------------------------------
  // 2. UI, Notifications & Examiner Audio (TTS)
  // -------------------------------------------------------------------------
  const escapeHtml = (str) => {
    const div = document.createElement('div');
    div.textContent = str ?? '';
    return div.innerHTML;
  };

  const countWords = (text) => (text || '').trim().split(/\s+/).filter(Boolean).length;

  const formatTime = (sec) =>
    `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`;

  function showView(view) {
    ['setup', 'exam', 'results'].forEach((v) =>
      $(`view-${v}`).classList.toggle('hidden', v !== view)
    );
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function showAlert(title, message, type = 'warning') {
    const styles = {
      error: 'bg-rose-50 border-rose-200 text-rose-900',
      success: 'bg-emerald-50 border-emerald-200 text-emerald-900',
      warning: 'bg-amber-50 border-amber-200 text-amber-900',
    };
    $('global-alert-title').textContent = title;
    $('global-alert-message').textContent = message;
    $('global-alert').className = `rounded-xl border p-4 shadow-sm transition-all animate-fade-in ${styles[type] || styles.warning}`;
  }

  const hideAlert = () => $('global-alert').classList.add('hidden');

  const EXAMINER_STATES = {
    speaking: {
      badge: 'bg-indigo-50 text-indigo-700 border-indigo-200 font-bold',
      dot: 'bg-indigo-600 animate-ping',
      text: 'Professor spricht...',
    },
    listening: {
      badge: 'bg-rose-50 text-rose-700 border-rose-200 font-bold',
      dot: 'bg-rose-600 animate-ping',
      text: 'Hört zu...',
    },
    pause: {
      badge: 'bg-amber-50 text-amber-800 border-amber-200 font-bold',
      dot: 'bg-amber-500 animate-pulse',
      text: 'Denkpause (3s Toleranz)...',
    },
    paused: {
      badge: 'bg-amber-50 text-amber-800 border-amber-200 font-bold',
      dot: 'bg-amber-500',
      text: 'Aufnahme pausiert',
    },
    evaluating: {
      badge: 'bg-purple-50 text-purple-700 border-purple-200 font-bold',
      dot: 'bg-purple-600 animate-ping',
      text: 'Bewertet Ihre Antwort...',
    },
    idle: {
      badge: 'bg-slate-100 text-slate-700 border-slate-200 font-semibold',
      dot: 'bg-emerald-500',
      text: 'Bereit für Ihre Antwort',
    },
  };

  function setExaminerState(mode, customText = null) {
    const cfg = EXAMINER_STATES[mode] || EXAMINER_STATES.idle;
    $('examiner-state-badge').className = `inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs border ${cfg.badge}`;
    $('examiner-state-dot').className = `w-2 h-2 rounded-full ${cfg.dot}`;
    $('examiner-state-text').textContent = customText || cfg.text;
    $('prof-speaking-ring').classList.toggle('hidden', mode !== 'speaking');
    $('btn-stop-audio').classList.toggle('hidden', mode !== 'speaking');
  }

  function stopCurrentAudio() {
    if (state.ttsAbort) {
      state.ttsAbort.abort();
      state.ttsAbort = null;
    }
    if (state.currentAudio) {
      state.currentAudio.pause();
      state.currentAudio = null;
    }
    if (state.currentAudioUrl) {
      URL.revokeObjectURL(state.currentAudioUrl);
      state.currentAudioUrl = null;
    }
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    if (!state.isRecording && !state.isEvaluating) setExaminerState('idle');
  }

  async function speakText(text, force = false) {
    if (!text?.trim() || (!state.autoPlayTts && !force)) return;
    stopCurrentAudio();
    setExaminerState('speaking');

    const controller = new AbortController();
    state.ttsAbort = controller;
    const timer = setTimeout(() => controller.abort(), 20000);

    try {
      const res = await apiFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.trim(), voice: state.voiceName, rate: '+0%' }),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const blob = await res.blob();
      if (controller.signal.aborted) return;

      state.currentAudioUrl = URL.createObjectURL(blob);
      const audio = new Audio(state.currentAudioUrl);
      state.currentAudio = audio;
      await new Promise((resolve) => {
        audio.onended = resolve;
        audio.onerror = resolve;
        audio.play().catch(resolve);
      });
    } catch (err) {
      clearTimeout(timer);
      if (err?.name === 'AbortError') return;
      if ('speechSynthesis' in window) {
        await new Promise((resolve) => {
          const utter = new SpeechSynthesisUtterance(text);
          utter.lang = 'de-DE';
          utter.onend = resolve;
          utter.onerror = resolve;
          window.speechSynthesis.speak(utter);
        });
      }
    } finally {
      if (state.currentAudioUrl) URL.revokeObjectURL(state.currentAudioUrl);
      state.currentAudioUrl = null;
      state.currentAudio = null;
      state.ttsAbort = null;
      if (!state.isRecording && !state.isEvaluating) setExaminerState('idle');
    }
  }

  function syncMuteUi() {
    $('chk-autoplay-tts').checked = state.autoPlayTts;
    $('icon-sound-on').classList.toggle('hidden', !state.autoPlayTts);
    $('icon-sound-off').classList.toggle('hidden', state.autoPlayTts);
    $('label-global-mute').textContent = state.autoPlayTts ? 'Audio: Ein' : 'Audio: Stumm';
    if (!state.autoPlayTts) stopCurrentAudio();
  }

  // -------------------------------------------------------------------------
  // 3. Microphone Check, Waveform Visualizer & Speech-to-Text (STT)
  // -------------------------------------------------------------------------
  function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const writeStr = (off, s) => [...s].forEach((ch, i) => view.setUint8(off + i, ch.charCodeAt(0)));

    writeStr(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeStr(8, 'WAVEfmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeStr(36, 'data');
    view.setUint32(40, samples.length * 2, true);

    samples.forEach((s, i) => {
      const clamped = Math.max(-1, Math.min(1, s));
      view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    });
    return new Blob([view], { type: 'audio/wav' });
  }

  async function runMicrophoneCheck() {
    if (state.micTestCleanup) {
      state.micTestCleanup();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);

      const data = new Uint8Array(analyser.frequencyBinCount);
      let frameId = null;
      $('label-test-mic').textContent = 'Test beenden';
      $('badge-mic-status').innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping"></span>Aktiv';

      const tick = () => {
        analyser.getByteFrequencyData(data);
        const avg = data.reduce((a, b) => a + b, 0) / data.length;
        $('mic-test-meter').style.width = `${Math.min(100, Math.max(4, Math.round((avg / 90) * 100)))}%`;
        frameId = requestAnimationFrame(tick);
      };
      tick();

      state.micTestCleanup = () => {
        cancelAnimationFrame(frameId);
        stream.getTracks().forEach((t) => t.stop());
        audioCtx.close().catch(() => {});
        $('mic-test-meter').style.width = '0%';
        $('label-test-mic').textContent = 'Mikrofon testen';
        $('badge-mic-status').innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>Bereit';
        state.micTestCleanup = null;
      };
      setTimeout(() => state.micTestCleanup && state.micTestCleanup(), 6000);
    } catch (_) {
      showAlert('Mikrofon nicht verfügbar', 'Bitte erlauben Sie den Mikrofonzugriff oder nutzen Sie die Texteingabe.', 'warning');
    }
  }

  function syncVoiceControls() {
    const words = countWords($('student-answer-input').value);
    const activeOrPaused = state.isRecording;

    $('btn-voice-pause').disabled = !activeOrPaused || state.isEvaluating;
    $('btn-voice-redo').disabled = (!activeOrPaused && words === 0) || state.isEvaluating;
    $('btn-voice-send').disabled = (!activeOrPaused && words === 0) || state.isEvaluating;

    $('label-voice-pause').textContent = state.isPaused ? 'Fortsetzen' : 'Pause';
  }

  async function startRecording() {
    if (state.isRecording || state.isEvaluating) return;
    stopCurrentAudio();

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    state.isRecording = true;
    state.isPaused = false;
    state.recordingSeconds = 0;
    state.committedText = $('student-answer-input').value.trim();

    $('waveform-container').classList.add('recording');
    $('record-btn-label').textContent = 'Aufnahme beenden';
    $('speech-live-status').classList.remove('hidden');
    $('speech-interim-text').textContent = 'Mikrofon aktiv – bitte sprechen Sie...';
    $('recording-timer').textContent = '00:00';
    setExaminerState('listening');
    syncVoiceControls();

    state.recordingInterval = setInterval(() => {
      if (state.isPaused) return;
      state.recordingSeconds += 1;
      $('recording-timer').textContent = formatTime(state.recordingSeconds);
      if (state.recordingSeconds >= 180) stopRecording();
    }, 1000);

    // Web Audio Visualizer + Always-on WAV backup recorder (in case Web Speech API fails or is unsupported)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.mediaStream = stream;
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      state.audioCtx = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);

      state.wavChunks = [];
      const proc = audioCtx.createScriptProcessor(4096, 1, 1);
      source.connect(proc);
      proc.connect(audioCtx.destination);
      proc.onaudioprocess = (e) => {
        if (state.isRecording && !state.isPaused) {
          state.wavChunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
        }
      };
      state.wavRecorder = { source, proc, sampleRate: audioCtx.sampleRate };

      const data = new Uint8Array(analyser.frequencyBinCount);
      const bars = document.querySelectorAll('#waveform-container .wave-bar');
      const draw = () => {
        if (!state.isRecording) return;
        if (state.isPaused) {
          bars.forEach((b) => { b.style.height = '5px'; });
        } else {
          analyser.getByteFrequencyData(data);
          bars.forEach((b, i) => {
            b.style.height = `${Math.max(5, Math.min(28, Math.round(((data[i % data.length] || 0) / 255) * 28)))}px`;
          });
        }
        state.animFrame = requestAnimationFrame(draw);
      };
      draw();
    } catch (_) {}

    if (SpeechRecognition) {
      const rec = new SpeechRecognition();
      rec.lang = 'de-DE';
      rec.continuous = true;
      rec.interimResults = true;

      rec.onresult = (event) => {
        if (state.isPaused) return;
        let interim = '';
        let finalChunk = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const piece = event.results[i][0].transcript;
          if (event.results[i].isFinal) finalChunk += `${piece} `;
          else interim += piece;
        }
        if (finalChunk.trim()) {
          state.committedText = [state.committedText, finalChunk.trim()].filter(Boolean).join(' ');
        }
        $('student-answer-input').value = [state.committedText, interim.trim()].filter(Boolean).join(' ');
        updateAnswerMetrics();
        $('speech-interim-text').textContent = interim.trim()
          ? `Erkannt: „${interim.trim()}“`
          : 'Sprechen Sie weiter (3s Denkpausen erlaubt)...';

        clearTimeout(state.pauseTimeout);
        $('pause-tolerance-pill').classList.add('hidden');
        setExaminerState('listening');
        state.pauseTimeout = setTimeout(() => {
          if (!state.isRecording || state.isPaused) return;
          $('pause-tolerance-pill').classList.remove('hidden');
          setExaminerState('pause');
        }, 1200);
      };

      rec.onend = () => {
        if (state.isRecording && !state.isPaused && state.recognition) {
          try { state.recognition.start(); } catch (_) {}
        }
      };

      state.recognition = rec;
      try { rec.start(); } catch (_) {}
    }
  }

  function togglePauseRecording() {
    if (!state.isRecording) return;
    state.isPaused = !state.isPaused;
    clearTimeout(state.pauseTimeout);
    $('pause-tolerance-pill').classList.add('hidden');

    if (state.isPaused) {
      state.committedText = $('student-answer-input').value.trim();
      if (state.recognition) {
        try { state.recognition.stop(); } catch (_) {}
      }
      $('waveform-container').classList.remove('recording');
      $('speech-interim-text').textContent = 'Aufnahme pausiert – klicken Sie auf „Fortsetzen“, um weiterzusprechen.';
      setExaminerState('paused');
    } else {
      $('waveform-container').classList.add('recording');
      $('speech-interim-text').textContent = 'Aufnahme fortgesetzt – bitte sprechen Sie weiter...';
      setExaminerState('listening');
      if (state.recognition) {
        try { state.recognition.start(); } catch (_) {}
      }
    }
    syncVoiceControls();
  }

  async function redoRecording() {
    if (state.isEvaluating) return;
    if (state.isRecording) {
      await stopRecording();
    }
    $('student-answer-input').value = '';
    state.committedText = '';
    state.shortAnswerConfirmed = false;
    $('recording-timer').textContent = '00:00';
    updateAnswerMetrics();
    await startRecording();
  }

  async function sendVoiceAnswer() {
    if (state.isEvaluating) return;
    hideAlert();
    state.shortAnswerConfirmed = true;

    const currentFullText = $('student-answer-input').value.trim();
    if (state.isRecording) {
      await stopRecording();
    }
    if (currentFullText && !$('student-answer-input').value.trim()) {
      $('student-answer-input').value = currentFullText;
    }
    updateAnswerMetrics();

    if (countWords($('student-answer-input').value) === 0) {
      showAlert(
        'Keine Sprache erkannt',
        'Es konnte noch kein Antworttext erkannt werden. Bitte sprechen Sie erneut oder tippen Sie Ihre Antwort in das Textfeld.',
        'warning'
      );
      return;
    }
    await submitAnswer(false);
  }

  async function stopRecording() {
    if (!state.isRecording) return;
    state.isRecording = false;
    state.isPaused = false;
    clearInterval(state.recordingInterval);
    clearTimeout(state.pauseTimeout);

    // Only wait briefly for SpeechRecognition flush if textarea is still empty
    if (state.recognition) {
      const rec = state.recognition;
      state.recognition = null;
      if ($('student-answer-input').value.trim()) {
        rec.onend = null;
        try { rec.stop(); } catch (_) {}
      } else {
        await new Promise((resolve) => {
          const timeout = setTimeout(resolve, 220);
          rec.onend = () => {
            clearTimeout(timeout);
            resolve();
          };
          try { rec.stop(); } catch (_) { clearTimeout(timeout); resolve(); }
        });
      }
    }

    const preservedText = $('student-answer-input').value.trim();

    if (state.animFrame) cancelAnimationFrame(state.animFrame);
    document.querySelectorAll('#waveform-container .wave-bar').forEach((b) => { b.style.height = '5px'; });
    if (state.wavRecorder) {
      try {
        state.wavRecorder.proc.disconnect();
        state.wavRecorder.source.disconnect();
      } catch (_) {}
    }
    if (state.mediaStream) state.mediaStream.getTracks().forEach((t) => t.stop());
    if (state.audioCtx) state.audioCtx.close().catch(() => {});

    $('waveform-container').classList.remove('recording');
    $('record-btn-label').textContent = 'Sprachaufnahme starten';
    $('speech-live-status').classList.add('hidden');
    $('pause-tolerance-pill').classList.add('hidden');
    if (preservedText) {
      $('student-answer-input').value = preservedText;
      state.committedText = preservedText;
    }

    // Automatic Server-STT Fallback: if Web Speech API produced no text, transcribe recorded WAV via /api/stt
    const hasAudioSamples = state.wavRecorder && state.wavChunks.length > 0;
    const sampleRate = state.wavRecorder ? state.wavRecorder.sampleRate : 44100;
    const recordedChunks = state.wavChunks.slice();
    state.wavRecorder = null;
    state.wavChunks = [];

    if (!preservedText && hasAudioSamples) {
      const total = recordedChunks.reduce((acc, c) => acc + c.length, 0);
      const merged = new Float32Array(total);
      let offset = 0;
      recordedChunks.forEach((c) => { merged.set(c, offset); offset += c.length; });
      const wavBlob = encodeWav(merged, sampleRate);

      const form = new FormData();
      form.append('audio', wavBlob, 'recording.wav');
      setExaminerState('evaluating', 'Transkribiere Sprachaufnahme...');
      try {
        const res = await apiFetch('/api/stt', { method: 'POST', body: form });
        const data = await res.json();
        if (res.ok && data.text) {
          $('student-answer-input').value = [$('student-answer-input').value.trim(), data.text].filter(Boolean).join(' ');
          state.committedText = $('student-answer-input').value.trim();
        }
      } catch (_) {}
    }

    setExaminerState('idle');
    updateAnswerMetrics();
  }

  // -------------------------------------------------------------------------
  // 4. Exam Session Flow & Evaluation
  // -------------------------------------------------------------------------
  function updateSetupSummary() {
    document.querySelectorAll('.mode-card').forEach((card) => {
      const active = card.dataset.mode === state.selectedMode;
      card.classList.toggle('active', active);
      card.classList.toggle('border-indigo-600', active);
      card.classList.toggle('border-slate-200', !active);
      card.querySelector('.mode-check').className = active
        ? 'mode-check w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center'
        : 'mode-check w-6 h-6 rounded-full border-2 border-slate-300 text-transparent flex items-center justify-center';
    });

    const fmt = (n) => `${n} ${n === 1 ? 'Frage' : 'Fragen'}`;
    $('badge-realistic-count').textContent = fmt(state.counts.realistic);
    $('badge-topic-count').textContent = fmt(state.counts.topic);
    $('badge-quick-count').textContent = fmt(state.counts.quick);

    const summaries = {
      realistic: `Realistische Prüfungssimulation · ${fmt(state.counts.realistic)} quer durch alle Themen`,
      topic: `Themen-Training: ${state.selectedTopic || 'Thema'} · ${fmt(state.counts.topic)}`,
      quick: `Schnelldurchlauf · Erste ${fmt(state.counts.quick)} der Reihe nach`,
    };
    $('setup-summary-text').textContent = summaries[state.selectedMode];
  }

  function updateAnswerMetrics() {
    const words = countWords($('student-answer-input').value);
    $('answer-word-count').textContent = `${words} ${words === 1 ? 'Wort' : 'Wörter'}`;
    $('btn-submit-answer').disabled = words === 0 || state.isEvaluating;
    $('btn-clear-answer').classList.toggle('hidden', words === 0);
    if (words === 0 || words > 3) {
      $('short-answer-warning').classList.add('hidden');
      state.shortAnswerConfirmed = false;
    }
    syncVoiceControls();
  }

  function getScoreColors(points, status) {
    if (status === 'Fehler') return { pill: 'bg-slate-100 text-slate-700 border-slate-300', tag: 'bg-slate-200 text-slate-800' };
    if (points >= 7) return { pill: 'bg-emerald-100 text-emerald-800 border-emerald-300', tag: 'bg-emerald-50 text-emerald-700 border border-emerald-200' };
    if (points >= 5) return { pill: 'bg-amber-100 text-amber-800 border-amber-300', tag: 'bg-amber-50 text-amber-800 border border-amber-200' };
    return { pill: 'bg-rose-100 text-rose-800 border-rose-300', tag: 'bg-rose-50 text-rose-700 border border-rose-200' };
  }

  async function startExamSession() {
    hideAlert();
    if (state.micTestCleanup) state.micTestCleanup();
    $('btn-start-exam').disabled = true;

    try {
      const res = await apiFetch('/api/exam/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: state.selectedMode,
          count: state.counts[state.selectedMode],
          topic: state.selectedMode === 'topic' ? state.selectedTopic : null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      state.sessionId = data.session_id;
      state.modeName = data.mode_name;
      state.questions = data.questions || [];
      state.currentIndex = 0;
      state.results = [];

      showView('exam');
      await renderCurrentQuestion(true);
    } catch (err) {
      showAlert('Startfehler', `Prüfung konnte nicht gestartet werden (${err.message}).`, 'error');
    } finally {
      $('btn-start-exam').disabled = false;
    }
  }

  async function renderCurrentQuestion(isFirst = false) {
    stopCurrentAudio();
    if (state.isRecording) await stopRecording();

    const q = state.questions[state.currentIndex];
    if (!q) return;

    const num = state.currentIndex + 1;
    const total = state.questions.length;
    $('exam-progress-label').textContent = `Frage ${num} von ${total}`;
    $('exam-topic-text').textContent = q.thema || 'Allgemein';
    $('exam-question-id').textContent = `ID #${q.id}`;
    $('exam-progress-bar').style.width = `${Math.round((num / total) * 100)}%`;
    $('exam-question-text').textContent = q.frage;

    const valid = state.results.filter((r) => r.status !== 'Fehler');
    const pts = valid.reduce((sum, r) => sum + Number(r.punkte || 0), 0);
    $('exam-running-score').textContent = `Punkte: ${pts} / ${valid.length * 10}`;
    $('label-finish-early').textContent = state.results.length > 0 ? 'Vorzeitig auswerten' : 'Prüfung abbrechen';

    $('student-answer-input').value = '';
    $('student-answer-input').disabled = false;
    state.committedText = '';
    state.shortAnswerConfirmed = false;
    $('recording-timer').textContent = '00:00';
    $('feedback-card').classList.add('hidden');
    updateAnswerMetrics();
    setExaminerState('idle');

    const prefix = isFirst ? 'Willkommen zur mündlichen Prüfung. ' : '';
    state.speechCache.question = `${prefix}Frage Nummer ${num}: ${q.frage}`;
    speakText(state.speechCache.question);
  }

  async function submitAnswer(skip = false) {
    if (state.isEvaluating) return;
    if (state.isRecording) await stopRecording();
    stopCurrentAudio();
    hideAlert();

    const text = $('student-answer-input').value.trim();
    const words = countWords(text);
    if (!skip && words === 0) return;

    if (!skip && words <= 3 && !state.shortAnswerConfirmed) {
      $('short-answer-warning').classList.remove('hidden');
      return;
    }

    $('short-answer-warning').classList.add('hidden');
    state.isEvaluating = true;
    $('btn-submit-answer').disabled = true;
    $('student-answer-input').disabled = true;
    $('submit-btn-label').textContent = 'Prüfer bewertet...';
    setExaminerState('evaluating');
    syncVoiceControls();

    // Show animated evaluation progress banner
    const evalBanner = $('eval-loading-banner');
    const evalBar = $('eval-loading-bar');
    const evalPct = $('eval-loading-pct');
    let pctVal = 12;
    if (evalBanner) {
      evalBanner.classList.remove('hidden');
      evalBar.style.width = '12%';
      evalPct.textContent = '12%';
    }
    const evalProgressTimer = setInterval(() => {
      pctVal = Math.min(94, pctVal + Math.max(2, Math.round((95 - pctVal) * 0.18)));
      if (evalBar) evalBar.style.width = `${pctVal}%`;
      if (evalPct) evalPct.textContent = `${pctVal}%`;
    }, 450);

    const q = state.questions[state.currentIndex];
    try {
      const res = await apiFetch('/api/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: state.sessionId,
          question_id: q.id,
          user_antwort: skip ? '' : text,
          frage: q.frage,
          musterloesung: q.musterloesung,
          thema: q.thema,
          skip,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const evalData = await res.json();
      const idx = state.results.findIndex((r) => Number(r.id) === Number(q.id));
      if (idx >= 0) state.results[idx] = evalData;
      else state.results.push(evalData);

      if (evalBar) evalBar.style.width = '100%';
      if (evalPct) evalPct.textContent = '100%';

      const valid = state.results.filter((r) => r.status !== 'Fehler');
      const pts = valid.reduce((sum, r) => sum + Number(r.punkte || 0), 0);
      $('exam-running-score').textContent = `Punkte: ${pts} / ${valid.length * 10}`;

      renderFeedbackCard(evalData);
    } catch (err) {
      showAlert('Bewertungsfehler', `Verbindungsproblem (${err.message}). Bitte erneut versuchen.`, 'error');
      $('student-answer-input').disabled = false;
      setExaminerState('idle');
    } finally {
      clearInterval(evalProgressTimer);
      if (evalBanner) evalBanner.classList.add('hidden');
      state.isEvaluating = false;
      $('submit-btn-label').textContent = 'Antwort bewerten lassen';
      updateAnswerMetrics();
    }
  }

  function renderFeedbackCard(ev) {
    const pts = Number(ev.punkte ?? 0);
    const colors = getScoreColors(pts, ev.status);

    $('feedback-score-pill').className = `inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-sm font-extrabold border ${colors.pill}`;
    $('feedback-score-pill').textContent = `${pts} / 10 Punkte`;
    $('feedback-status-tag').className = `inline-flex items-center px-3 py-1 rounded-lg text-xs font-bold uppercase tracking-wider ${colors.tag}`;
    $('feedback-status-tag').textContent = ev.status || 'Teilweise';
    $('btn-retry-eval').classList.toggle('hidden', ev.status !== 'Fehler');

    $('feedback-spoken-text').textContent = ev.gesprochenes_feedback || '';
    $('feedback-missing-text').textContent = ev.was_fehlt || 'Keine wesentlichen Lücken festgestellt.';
    $('feedback-merksatz-text').textContent = ev.merksatz || 'Die Kernkonzepte sitzen solide.';
    $('feedback-musterloesung-text').textContent = ev.musterloesung || '';

    const isLast = state.currentIndex >= state.questions.length - 1;
    $('next-question-label').textContent = isLast ? 'Prüfung abschließen & Endnote berechnen' : 'Nächste Frage';
    $('feedback-card').classList.remove('hidden');
    $('feedback-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    state.speechCache.feedback = ev.gesprochenes_feedback || '';
    speakText(state.speechCache.feedback);
  }

  // -------------------------------------------------------------------------
  // 5. Results Dashboard & Event Bindings
  // -------------------------------------------------------------------------
  async function finishExam() {
    stopCurrentAudio();
    if (state.isRecording) await stopRecording();
    if (state.results.length === 0) return showView('setup');

    $('btn-next-question').disabled = true;
    try {
      const res = await apiFetch('/api/exam/finish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: state.sessionId,
          mode_name: state.modeName,
          results: state.results,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const summary = await res.json();
      renderResultsDashboard(summary);
      showView('results');
      state.speechCache.closing = summary.closing_remarks || '';
      speakText(state.speechCache.closing);
    } catch (err) {
      showAlert('Auswertungsfehler', err.message, 'error');
    } finally {
      $('btn-next-question').disabled = false;
    }
  }

  function renderResultsDashboard(s) {
    const fmtDec = (n) => Number(n || 0).toFixed(1).replace('.', ',');
    $('results-pass-badge').textContent = s.passed ? 'Prüfung Bestanden' : 'Nicht Bestanden';
    $('results-mode-badge').textContent = s.mode_name || '';
    $('results-timestamp').textContent = s.timestamp || '';
    $('results-closing-remarks').textContent = s.closing_remarks || '';
    $('results-grade-number').textContent = fmtDec(s.grade);
    $('results-grade-label').textContent = s.grade_label || '';

    $('metric-total-score').textContent = `${s.total_points} / ${s.max_points} Punkte`;
    $('metric-percentage').textContent = `${fmtDec(s.percentage)} %`;
    $('metric-questions-answered').textContent = `${s.questions_answered} / ${s.questions_total}`;
    const avg = s.valid_questions_count > 0 ? s.total_points / s.valid_questions_count : 0;
    $('metric-avg-points').textContent = `Durchschnitt: ${fmtDec(avg)} / 10 Pkt.`;

    const renderBadges = (items, cls, emptyMsg) =>
      items?.length
        ? items.map((x) => `<span class="px-3 py-1 rounded-lg text-xs font-bold bg-white border ${cls}">${escapeHtml(x)}</span>`).join('')
        : `<span class="text-xs italic opacity-75">${emptyMsg}</span>`;

    $('results-strengths-list').innerHTML = renderBadges(s.strengths, 'text-emerald-800 border-emerald-200', 'Keine Themen mit Ø ≥ 7,5 / 10.');
    $('results-weaknesses-list').innerHTML = renderBadges(s.weaknesses, 'text-rose-800 border-rose-200', 'Keine gravierenden Schwächen (≤ 5,0 / 10).');

    $('results-topic-bars').innerHTML = (s.topic_breakdown || [])
      .map((t) => {
        const pct = Math.min(100, Math.round((t.average / 10) * 100));
        const color = t.average >= 7.5 ? 'bg-emerald-500' : t.average <= 5.0 ? 'bg-rose-500' : 'bg-amber-500';
        return `
          <div class="space-y-1">
            <div class="flex justify-between text-xs">
              <span class="font-semibold text-slate-800">${escapeHtml(t.thema)} (${t.count})</span>
              <span class="font-mono font-bold text-slate-700">Ø ${fmtDec(t.average)} / 10 Pkt.</span>
            </div>
            <div class="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
              <div class="h-full ${color} rounded-full" style="width:${pct}%"></div>
            </div>
          </div>`;
      })
      .join('');

    $('results-transcript-list').innerHTML = (s.results || [])
      .map((r, i) => {
        const pts = Number(r.punkte ?? 0);
        const colors = getScoreColors(pts, r.status);
        return `
          <details class="transcript-item group rounded-xl border border-slate-200 bg-slate-50/40 overflow-hidden" ${i === 0 ? 'open' : ''}>
            <summary class="flex flex-wrap items-center justify-between gap-3 px-4 py-3.5 cursor-pointer select-none hover:bg-slate-100/80">
              <div class="flex items-center gap-2 min-w-0">
                <span class="px-2 py-0.5 rounded text-xs font-bold bg-slate-900 text-white">#${i + 1}</span>
                <span class="px-2.5 py-0.5 rounded text-xs font-semibold bg-indigo-50 text-indigo-700">${escapeHtml(r.thema)}</span>
                <span class="text-xs sm:text-sm font-bold text-slate-800 truncate">${escapeHtml(r.frage)}</span>
              </div>
              <span class="px-2.5 py-0.5 rounded-full text-xs font-extrabold border ${colors.pill}">${pts} / 10 Pkt.</span>
            </summary>
            <div class="p-4 bg-white border-t border-slate-200 space-y-3 text-xs sm:text-sm">
              <p class="font-semibold text-slate-900">${escapeHtml(r.frage)}</p>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div class="p-3 rounded-lg bg-slate-50 border border-slate-200">
                  <p class="text-[11px] font-bold uppercase text-slate-500">Ihre Antwort</p>
                  <p class="mt-1">${r.user_antwort ? escapeHtml(r.user_antwort) : '<em>(Keine Antwort)</em>'}</p>
                </div>
                <div class="p-3 rounded-lg bg-emerald-50/40 border border-emerald-200">
                  <p class="text-[11px] font-bold uppercase text-emerald-800">Musterlösung</p>
                  <p class="mt-1">${escapeHtml(r.musterloesung)}</p>
                </div>
              </div>
              <div class="p-3 rounded-lg bg-indigo-50/40 border border-indigo-100 space-y-1">
                <p><strong>Prüfer-Feedback:</strong> ${escapeHtml(r.gesprochenes_feedback)}</p>
                <p><strong>Fehlende Aspekte:</strong> ${escapeHtml(r.was_fehlt || 'Keine')}</p>
                ${r.merksatz ? `<p class="italic text-indigo-900"><strong>Merksatz:</strong> ${escapeHtml(r.merksatz)}</p>` : ''}
              </div>
            </div>
          </details>`;
      })
      .join('');

    if (s.download_url) {
      $('btn-download-protocol').href = apiUrl(s.download_url);
      $('btn-download-protocol').setAttribute('download', s.report_filename || 'protokoll.md');
      $('label-download-protocol').textContent = `Markdown-Protokoll herunterladen (${s.report_filename})`;
    }
  }

  async function init() {
    try {
      const res = await apiFetch('/api/topics');
      const data = await res.json();
      state.topics = data.topics || [];
      $('stat-total-questions').textContent = String(data.total_questions || 100);
      $('stat-total-topics').textContent = String(state.topics.length);

      if (data.config) {
        state.voiceName = data.config.voice_name || state.voiceName;
        $('nav-model-name').textContent = data.config.model_name || 'LLM';
        $('nav-voice-select').value = state.voiceName;
      }

      $('select-topic-name').innerHTML = state.topics
        .map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)} (${t.count} Fragen)</option>`)
        .join('');
      state.selectedTopic = state.topics[0]?.name || '';
      updateSetupSummary();
    } catch (_) {
      showAlert('Verbindungsfehler', 'Bitte stellen Sie sicher, dass "python app.py" läuft.', 'error');
    }

    // Setup bindings
    document.querySelectorAll('.mode-card').forEach((card) =>
      card.addEventListener('click', () => {
        state.selectedMode = card.dataset.mode;
        updateSetupSummary();
      })
    );

    ['realistic', 'topic', 'quick'].forEach((mode) => {
      $(`slider-${mode}-count`).addEventListener('input', (e) => {
        state.selectedMode = mode;
        state.counts[mode] = Number(e.target.value);
        updateSetupSummary();
      });
    });

    $('select-topic-name').addEventListener('change', (e) => {
      state.selectedMode = 'topic';
      state.selectedTopic = e.target.value;
      const max = state.topics.find((t) => t.name === state.selectedTopic)?.count || 10;
      $('slider-topic-count').max = String(max);
      if (state.counts.topic > max) state.counts.topic = max;
      updateSetupSummary();
    });

    // Global & Setup Audio/Mic bindings
    $('nav-voice-select').addEventListener('change', (e) => { state.voiceName = e.target.value; });
    $('btn-global-mute').addEventListener('click', () => { state.autoPlayTts = !state.autoPlayTts; syncMuteUi(); });
    $('chk-autoplay-tts').addEventListener('change', (e) => { state.autoPlayTts = e.target.checked; syncMuteUi(); });
    $('btn-close-alert').addEventListener('click', hideAlert);
    $('btn-test-mic').addEventListener('click', runMicrophoneCheck);
    $('btn-test-tts').addEventListener('click', () =>
      speakText('Guten Tag! Die Sprachausgabe für Ihre mündliche Prüfung ist bereit.', true)
    );
    $('btn-start-exam').addEventListener('click', startExamSession);

    // Active Exam bindings
    $('btn-replay-question').addEventListener('click', () => speakText(state.speechCache.question, true));
    $('btn-stop-audio').addEventListener('click', stopCurrentAudio);
    $('btn-record-toggle').addEventListener('click', () => (state.isRecording ? stopRecording() : startRecording()));
    $('btn-voice-pause').addEventListener('click', togglePauseRecording);
    $('btn-voice-redo').addEventListener('click', redoRecording);
    $('btn-voice-send').addEventListener('click', sendVoiceAnswer);
    $('student-answer-input').addEventListener('input', (e) => {
      state.committedText = e.target.value;
      updateAnswerMetrics();
    });
    $('student-answer-input').addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !$('btn-submit-answer').disabled) {
        e.preventDefault();
        submitAnswer(false);
      }
    });
    $('btn-clear-answer').addEventListener('click', () => {
      $('student-answer-input').value = '';
      state.committedText = '';
      updateAnswerMetrics();
    });
    $('btn-confirm-short-submit').addEventListener('click', () => {
      state.shortAnswerConfirmed = true;
      submitAnswer(false);
    });
    $('btn-submit-answer').addEventListener('click', () => submitAnswer(false));
    $('btn-skip-question').addEventListener('click', () => {
      if (confirm('Frage wirklich ohne Antwort überspringen (0 Punkte)?')) submitAnswer(true);
    });
    $('btn-replay-feedback').addEventListener('click', () => speakText(state.speechCache.feedback, true));
    $('btn-retry-eval').addEventListener('click', () => {
      state.shortAnswerConfirmed = true;
      submitAnswer(false);
    });
    $('btn-next-question').addEventListener('click', () => {
      if (state.currentIndex < state.questions.length - 1) {
        state.currentIndex += 1;
        renderCurrentQuestion(false);
      } else {
        finishExam();
      }
    });
    $('btn-finish-early').addEventListener('click', () => {
      if (state.results.length === 0) showView('setup');
      else if (confirm('Bisherige Antworten jetzt auswerten?')) finishExam();
    });

    // Results bindings
    $('btn-replay-closing').addEventListener('click', () => speakText(state.speechCache.closing, true));
    $('btn-toggle-all-transcript').addEventListener('click', () => {
      const items = [...document.querySelectorAll('details.transcript-item')];
      const allOpen = items.every((d) => d.open);
      items.forEach((d) => { d.open = !allOpen; });
      $('btn-toggle-all-transcript').textContent = allOpen ? 'Alle aufklappen' : 'Alle zuklappen';
    });
    $('btn-new-exam').addEventListener('click', () => {
      stopCurrentAudio();
      showView('setup');
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
