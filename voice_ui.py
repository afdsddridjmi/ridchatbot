"""Composant Streamlit v2 : lecture vocale, détection d'activité, transcription visible."""
from pathlib import Path
import streamlit as st

JS = (Path(__file__).parent / "voice_component.js").read_text(encoding="utf-8")
HTML = """
<div class="rid-shell">
  <section class="rid-transcript-card" aria-label="Transcription de l'entretien">
    <div class="rid-transcript-heading">Transcription de l'entretien <span id="rid-message-count"></span></div>
    <div class="rid-transcript-body" id="rid-transcript" role="log" aria-label="Messages échangés" aria-live="off"></div>
  </section>
  <div class="rid-voice-panel">
    <p id="rid-status" role="status" aria-live="polite">Préparation de l'entretien…</p>
    <p id="rid-meter" hidden>À l'écoute…</p>
    <div class="rid-actions">
      <button id="rid-replay" type="button">🔊 Répéter la question</button>
      <button id="rid-send" type="button" disabled>✓ Question suivante · Entrée</button>
      <button id="rid-finish" type="button">Je souhaite conclure</button>
    </div>
    <p class="rid-help">Après chaque question, le microphone s'ouvre automatiquement si le navigateur l'autorise. Votre réponse se termine après 5 secondes de silence, ou avec Entrée.</p>
  </div>
</div>
"""
CSS = """
.rid-shell {font-family:inherit; width:100%;}
.rid-transcript-card {border:1px solid var(--st-border-color,#cbd5df);background:var(--st-background-color,#fff);border-radius:14px 14px 0 0;overflow:hidden;}
.rid-transcript-heading {font-weight:650; padding:10px 13px; display:flex; justify-content:space-between; background:var(--st-secondary-background-color,#f6f8fa);}
#rid-message-count {font-size:.77rem;font-weight:400;opacity:.65;}
.rid-transcript-body {overflow-y:auto; min-height:86px; max-height:clamp(100px,31dvh,330px);overscroll-behavior:contain;padding:9px 12px;scrollbar-gutter:stable;}
.rid-bubble {margin:0 0 8px;padding:8px 10px;border-radius:10px;line-height:1.35;overflow-wrap:anywhere;white-space:pre-wrap;}
.rid-bubble.rri {margin-left:14%;background:#e8f2fa;color:#132d43;}
.rid-bubble.bot {margin-right:12%;background:var(--st-secondary-background-color,#f3f4f5);color:var(--st-text-color,#202020);}
.rid-bubble strong {font-size:.77rem;display:block;margin-bottom:3px;opacity:.75;}
.rid-voice-panel {border:1px solid var(--st-border-color,#cbd5df); border-top:none;border-radius:0 0 14px 14px;padding:12px 14px;background:var(--st-secondary-background-color,#f7f8f9);}
#rid-status {font-weight:600;margin:0 0 5px;line-height:1.3;font-size:.95rem;}
#rid-meter {color:#136b50;font-size:.84rem;margin:0 0 9px;}
.rid-actions {display:flex;flex-wrap:wrap;gap:7px;align-items:center;}
.rid-actions button {border-radius:9px;padding:9px 10px;border:1px solid #8898a6;background:var(--st-background-color,#fff);color:var(--st-text-color,#202020);cursor:pointer;font-size:.88rem;line-height:1.3;}
.rid-actions button:hover {border-color:#1e70ab;}
.rid-actions button:disabled {opacity:.47;cursor:not-allowed;}
#rid-replay {border-color:#287cad;}
#rid-finish {margin-left:auto;}
#rid-meter[hidden] {display:none !important;}
.rid-help {font-size:.74rem;opacity:.72;margin:9px 0 0;line-height:1.35;}
@media (max-width:560px) {.rid-transcript-body{max-height:28dvh;}.rid-actions button{font-size:.81rem;padding:9px 8px;}#rid-finish{margin-left:0;}}
"""
voice_component = st.components.v2.component("rid_vocal_turn_v3", html=HTML, css=CSS, js=JS)


def voice_turn(question: str, turn_id: str, history: list[dict[str, str]], is_confirmation: bool):
    """Renvoie audio / finish_request dans des événements Streamlit v2."""
    return voice_component(
        data={"question": question, "turnId": turn_id,
              "history": [{"role": m["role"], "content": m["content"]} for m in history],
              "confirmation": is_confirmation},
        key="rid_microphone", on_audio_change=lambda: None,
        on_finish_request_change=lambda: None,
    )
