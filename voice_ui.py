"""Composant voix v2 sans iframe : retour d'un fichier WAV au serveur Streamlit."""
from pathlib import Path
import streamlit as st

JS = (Path(__file__).parent / "voice_component.js").read_text(encoding="utf-8")
HTML = """
<div class="rid-voice-panel">
  <div class="rid-prompt" id="rid-prompt" aria-live="polite"></div>
  <p id="rid-status" role="status" aria-live="polite">Prêt à commencer.</p>
  <p id="rid-meter" hidden>À l'écoute…</p>
  <div class="rid-actions">
    <button id="rid-start" type="button" class="rid-primary">▶ Démarrer l'entretien vocal</button>
    <button id="rid-replay" type="button" hidden>🔊 Répéter la question</button>
    <button id="rid-send" type="button" hidden>✓ Passer à la question suivante · Entrée</button>
    <button id="rid-finish" type="button">Je souhaite conclure</button>
  </div>
  <p class="rid-help">L'enregistrement commence après la question. Il s'arrête après 5 secondes de silence, ou avec Entrée.</p>
</div>
"""
CSS = """
.rid-voice-panel {border:1px solid var(--st-border-color, #ccd0d5);border-radius:18px;padding:20px;background:var(--st-secondary-background-color,#f7f8f9);font-family:inherit;}
.rid-prompt {font-size:1.14rem;font-weight:650;line-height:1.5;}
#rid-status {margin:16px 0 10px;color:var(--st-text-color, #273345);}
#rid-meter {color:#0b7654;font-size:.95rem;font-weight:600;}
.rid-actions {display:flex;flex-wrap:wrap;gap:8px;align-items:center;}
.rid-actions button {border-radius:10px;padding:10px 13px;border:1px solid #8b9ba7;background:var(--st-background-color,#fff);color:var(--st-text-color,#222);cursor:pointer;font-size:.95rem;}
.rid-actions button:hover {border-color:#356dad;}
.rid-actions button.rid-primary {background:#12649b;color:#fff;border-color:#12649b;font-weight:650;}
.rid-actions button[hidden], #rid-meter[hidden] {display:none !important;}
.rid-actions button:disabled {opacity:.5;cursor:not-allowed;}
.rid-help {font-size:.82rem;opacity:.78;margin:12px 0 0;}
"""
voice_component = st.components.v2.component("rid_vocal_turn_v2", html=HTML, css=CSS, js=JS)


def voice_turn(question: str, turn_id: str, auto_start: bool):
    """Retourne result.audio ou result.finish_request (événements à usage unique)."""
    return voice_component(data={"question": question, "turnId": turn_id,
                                 "autoStart": auto_start},
                           key="rid_microphone", on_audio_change=lambda: None,
                           on_finish_request_change=lambda: None)
