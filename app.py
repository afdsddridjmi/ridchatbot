"""La voix des correspondants : micro -> Albert ASR -> Albert chat -> voix navigateur.

Code et audio non sauvegardés par l'application (session Streamlit seulement).
Le serveur Streamlit et Albert traitent néanmoins les données ; voir README.
"""
from __future__ import annotations

import hmac
import hashlib
import html
import json
from datetime import datetime, timezone
from urllib.parse import quote

import streamlit as st

from albert_api import AlbertError, DEFAULT_ASR_MODEL, DEFAULT_CHAT_MODEL, chat, transcribe

st.set_page_config(page_title="La voix des correspondants", page_icon="🎙️", layout="centered")

MAX_TURNS = 7
MAX_TEXT_CHARS = 1500
MAX_SUMMARY_CHARS = 950
FIRST_QUESTION = "Bonjour ! Avec un peu de recul, qu'est-ce que tu retiens de notre dernière réunion ?"


def setting(name: str, default: str = "") -> str:
    return str(st.secrets.get(name, default)).strip()


def meeting_name() -> str:
    return setting("MEETING_LABEL", "notre dernière réunion")[:100]


def question_focus() -> str:
    return setting("QUESTION_CIBLEE", "Quel sujet voudrais-tu mettre en priorité à l'ordre du jour de notre prochaine réunion, et pourquoi ?")[:400]


def system_prompt(user_turns: int) -> str:
    return f"""Tu es un facilitateur IA d'écoute pour un réseau interne de trente correspondants.
Tu conduis un entretien ORAL (une question par prise de parole) à propos de {meeting_name()}.
Règles : français naturel, phrases COURTES faciles à écouter ; deux phrases au maximum par réponse.
Pose UNE question à la fois, écoute et relance ce qui est significatif : utilité, attentes,
irritants, difficultés à relayer les messages dans les divisions, idées concrètes.
Évite les commentaires stéréotypés et ne défends pas les organisateurs. Ne suppose pas de sentiment.
N'exige pas de noms, de divisions, de dossiers internes ni d'éléments personnels. N'en répète pas.
La première question a déjà été posée par l'application : ne recommence pas.
Ne produis pas de synthèse : elle est générée séparément à la demande.
QUESTION CIBLÉE À POSER avant de finir : {question_focus()}
À partir de la troisième intervention du participant, assure-toi que cette question ciblée
est posée à la prochaine occasion si elle ne l'a pas déjà été.
Ne pose pas une deuxième fois cette question si elle figure déjà dans la conversation.
Si le participant souhaite arrêter, remercie-le brièvement.
Interventions utilisateur précédentes : {user_turns} ; au plus {MAX_TURNS} prises de parole au total.
"""


def summary_prompt() -> str:
    return """Tu écris une SYNTHÈSE destinée aux animateurs du réseau, en français, uniquement
à partir des PROPOS explicitement exprimés par ce participant. Trois rubriques, maximum 700 caractères :
Ce qui a été utile : ...
Ce qui serait à améliorer : ...
Priorité proposée pour la prochaine réunion : ...
Si une rubrique n'est pas renseignée, écrire « Non exprimé ».
Supprime ou généralise les noms, divisions, dates ou détails reconnaissables et les informations
personnelles ; ne crée aucun fait. La synthèse sera relue et modifiée par son auteur.
Aucun prénom, aucune formule de politesse, aucun jugement psychologique, pas de promesse d'anonymat."""


def send_to_albert(history: list[dict[str, str]], max_tokens: int = 200,
                   for_summary: bool = False) -> str:
    prompt = summary_prompt() if for_summary else system_prompt(
        sum(m["role"] == "user" for m in history)
    )
    return chat(setting("ALBERT_API_KEY"),
                [{"role": "system", "content": prompt}] + history[-17:],
                model=setting("ALBERT_CHAT_MODEL", DEFAULT_CHAT_MODEL), max_tokens=max_tokens)


def reset_conversation() -> None:
    for name in list(st.session_state.keys()):
        if name in ("history", "summary", "editable_summary", "pending", "call_now") or name.startswith(("voice_", "message_", "audio_attempt_")):
            st.session_state.pop(name, None)


def tts_button(sentence: str) -> None:
    """Bouton de lecture vocale 100 % côté navigateur : aucun appel à une API TTS ici.

    On n'automatise pas la lecture : navigateurs et appareils exigent souvent un clic.
    Le texte est échappé comme littéral JavaScript, jamais injecté comme HTML non fiable.
    """
    literal = json.dumps(sentence[:1500], ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
    html = """<div style="font:14px system-ui, sans-serif;display:flex;align-items:center;gap:10px">
    <button id="speak" style="padding:8px 15px;border-radius:10px;border:1px solid #888;background:#f2f5fa;cursor:pointer">
      🔊 Écouter la question
    </button><span id="status" style="color:#666;font-size:12px"></span></div>
    <script>
    const content = __TEXT__;
    const btn = document.getElementById('speak');
    const status = document.getElementById('status');
    if (!('speechSynthesis' in window)) {
      btn.disabled = true; status.textContent = 'Voix non prise en charge par ce navigateur.';
    } else {
      btn.addEventListener('click', () => {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(content);
        u.lang = 'fr-FR'; u.rate = 0.96; u.pitch = 1.0;
        const voices = window.speechSynthesis.getVoices();
        const french = voices.find(v => v.lang.toLowerCase() === 'fr-fr')
          || voices.find(v => v.lang.toLowerCase().startsWith('fr'));
        if (french) u.voice = french;
        u.onstart = () => { status.textContent = 'Lecture en cours…'; };
        u.onend = () => { status.textContent = ''; };
        u.onerror = () => { status.textContent = 'Lecture indisponible sur cet appareil.'; };
        window.speechSynthesis.speak(u);
      });
    }
    </script>""".replace("__TEXT__", literal)
    st.iframe(html, height=60)


def record_user_message(text: str) -> None:
    cleaned = text.strip()[:MAX_TEXT_CHARS]
    if not cleaned:
        st.warning("Aucune réponse à envoyer.")
        return
    st.session_state.history.append({"role": "user", "content": cleaned})
    st.session_state.pending = True
    st.session_state.call_now = True
    st.rerun()


def mailto_link(email: str, summary: str) -> str:
    subject = f"Retour collectif après {meeting_name()}"
    body = "Bonjour,\n\nVoici mes éléments de retour, que j'ai vérifiés avant envoi :\n\n" + summary + "\n"
    return ("mailto:" + quote(email, safe="@+.-")
            + "?subject=" + quote(subject, safe="")
            + "&body=" + quote(body, safe=""))


st.title("La voix des correspondants")
st.caption("Un échange vocal libre, à tour de rôle — environ 5 minutes")
required = ("ALBERT_API_KEY", "ACCESS_CODE", "RESULT_EMAIL", "OPEN_UNTIL_UTC")
missing = [name for name in required if not setting(name)]
if missing:
    st.error("Configuration manquante : " + ", ".join(missing) + ". Contactez l'organisateur.")
    st.stop()

try:
    closing = datetime.fromisoformat(setting("OPEN_UNTIL_UTC").replace("Z", "+00:00"))
    if closing.tzinfo is None:
        raise ValueError("Fuseau manquant")
except ValueError:
    st.error("Date de clôture OPEN_UNTIL_UTC incorrecte dans la configuration.")
    st.stop()

if datetime.now(timezone.utc) > closing:
    st.info("Cette campagne de recueil est terminée.")
    st.stop()

if not st.session_state.get("approved", False):
    st.markdown("""Parlez à une IA qui écoute votre retour sur la réunion, puis vous propose une courte
synthèse à relire. **Vous choisissez ensuite de l'envoyer ou non par e-mail.**

L'audio et les transcriptions passent par **Streamlit Community Cloud** et **Albert API**.
Le programme ne constitue pas de base de données, mais ces fournisseurs peuvent traiter ou
journaliser des données techniques selon leurs conditions. **Ne communiquez aucun nom, division,
dossier sensible ou élément permettant d'identifier une personne.**

L'e-mail final part de **votre messagerie** : les animateurs voient votre adresse et le message
est donc **identifiable par eux**. Seule la restitution au réseau est prévue sous forme collective.
La participation est volontaire et vous pouvez quitter la page sans envoi.""")
    provided_code = st.text_input("Code de campagne communiqué dans le mail", type="password")
    consent = st.checkbox("J'ai compris les conditions et je souhaite participer.")
    if st.button("Commencer", type="primary", disabled=not (provided_code and consent)):
        attempts = st.session_state.get("failed_attempts", 0)
        if attempts >= 5:
            st.error("Trop de tentatives dans cette session. Fermez la page et contactez l'organisateur.")
        elif hmac.compare_digest(provided_code, setting("ACCESS_CODE")):
            st.session_state.approved = True
            st.rerun()
        else:
            st.session_state.failed_attempts = attempts + 1
            st.error("Code incorrect.")
    st.stop()

if "history" not in st.session_state:
    st.session_state.history = [{"role": "assistant", "content": FIRST_QUESTION}]

c1, c2 = st.columns([1, 1])
with c1:
    if st.button("Effacer l'échange", help="Efface les éléments de cette session applicative"):
        reset_conversation()
        st.rerun()
with c2:
    st.caption("Aucun envoi sans votre validation")

for message in st.session_state.history:
    with st.chat_message(message["role"]):
        st.write(message["content"])

if st.session_state.history[-1]["role"] == "assistant" and "summary" not in st.session_state:
    tts_button(st.session_state.history[-1]["content"])
    st.caption("La voix est celle de votre navigateur ; cliquez sur « Écouter la question ». ")

user_turns = sum(m["role"] == "user" for m in st.session_state.history)

if st.session_state.get("pending", False):
    if st.session_state.get("call_now", False):
        st.session_state.call_now = False
        with st.spinner("Albert prépare sa relance…"):
            try:
                reply = send_to_albert(st.session_state.history)
                st.session_state.history.append({"role": "assistant", "content": reply})
                st.session_state.pending = False
                st.rerun()
            except AlbertError as exc:
                st.error(str(exc))
    if st.button("Réessayer la relance"):
        st.session_state.call_now = True
        st.rerun()
    st.info("Votre dernière réponse reste dans la session. Vous pouvez réessayer sans la réenregistrer.")
    # La synthèse reste possible même si la dernière relance a échoué.

if "summary" not in st.session_state:
    if user_turns < MAX_TURNS and not st.session_state.get("pending", False):
        st.markdown(f"**À vous de répondre** · intervention {user_turns + 1}/{MAX_TURNS}")
        vocal_tab, text_tab = st.tabs(["🎤 Parler", "⌨️ Écrire (secours)"])
        with vocal_tab:
            st.caption("Appuyez sur le micro, parlez, puis arrêtez : la transcription démarre automatiquement.")
            recording = st.audio_input("Enregistrer votre réponse (90 secondes maximum)",
                                       sample_rate=16000, key=f"voice_{user_turns}")
            if recording is not None:
                data = recording.getvalue()
                audio_hash = hashlib.sha256(data).hexdigest()
                attempt_key = f"audio_attempt_{user_turns}"
                if st.session_state.get(attempt_key) != audio_hash:
                    # Empêche la même prise de parole d'être envoyée à nouveau sur un rerun.
                    st.session_state[attempt_key] = audio_hash
                    with st.spinner("Albert transcrit votre voix…"):
                        try:
                            result = transcribe(setting("ALBERT_API_KEY"), data,
                                                model=setting("ALBERT_ASR_MODEL", DEFAULT_ASR_MODEL))
                            record_user_message(result)
                        except AlbertError as exc:
                            st.error(str(exc))
                            st.info("Réenregistrez votre réponse, réessayez ou choisissez Écrire.")
                elif st.button("Réessayer la transcription", key=f"retry_audio_{user_turns}"):
                    st.session_state.pop(attempt_key, None)
                    st.rerun()
        with text_tab:
            with st.form(key=f"text_form_{user_turns}", clear_on_submit=True):
                written = st.text_area("Votre réponse", max_chars=MAX_TEXT_CHARS,
                                       placeholder="Vous pouvez répondre par écrit si vous préférez.")
                submitted = st.form_submit_button("Envoyer mon texte")
            if submitted:
                record_user_message(written)
    elif user_turns >= MAX_TURNS:
        st.info("La limite de prises de parole de ce pilote est atteinte. Vous pouvez préparer votre synthèse.")

    if user_turns >= 1 and st.button("Terminer et préparer ma synthèse", type="primary"):
        with st.spinner("Albert rédige une synthèse des propos exprimés…"):
            try:
                result = send_to_albert(st.session_state.history, max_tokens=350,
                                        for_summary=True)[:MAX_SUMMARY_CHARS]
                st.session_state.summary = result
                st.session_state.editable_summary = result
                st.rerun()
            except AlbertError as exc:
                st.error(str(exc))
else:
    st.divider()
    st.subheader("Votre synthèse à vérifier")
    st.info("Vous pouvez corriger, raccourcir ou supprimer tout détail reconnaissable.")
    st.text_area("Texte qui sera placé dans le brouillon de mail", key="editable_summary",
                 height=190, max_chars=MAX_SUMMARY_CHARS)
    summary = st.session_state.editable_summary.strip()
    if summary:
        st.markdown("**Copie de secours :**")
        st.code(summary, language=None)
        safe_mailto = html.escape(mailto_link(setting("RESULT_EMAIL"), summary), quote=True)
        st.markdown(
            f'<a href="{safe_mailto}" style="display:inline-block;padding:10px 15px;'
            'border-radius:8px;background:#1459a5;color:white;text-decoration:none;'
            'font-weight:600">Ouvrir un brouillon d’e-mail</a>',
            unsafe_allow_html=True,
        )
        st.caption("Le bouton ouvre le logiciel mail associé aux liens mailto sur cet appareil."
                   " L'envoi n'est jamais automatique. Si Outlook ne s'ouvre pas, copiez le texte affiché.")
    if st.button("Nouvel échange"):
        reset_conversation()
        st.rerun()
