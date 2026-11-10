"""Robavardeur RID : entretien vocal semi-automatique pour les RRI de SDD/AFD."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

import streamlit as st
from albert_api import AlbertError, DEFAULT_ASR_MODEL, DEFAULT_CHAT_MODEL, chat, transcribe
from voice_ui import voice_turn

st.set_page_config(page_title="Le robavardeur RID", page_icon="🎙️", layout="centered")
MAX_DIALOGUE_TURNS = 7
MAX_TEXT_CHARS = 1600
MAX_SUMMARY_CHARS = 1250
FIRST_QUESTION = "Bonjour ! Avec un peu de recul, qu'est-ce que tu retiens de notre dernière réunion des RRI ?"


def setting(name: str, default: str = "") -> str:
    return str(st.secrets.get(name, default)).strip()


def meeting_name() -> str:
    return setting("MEETING_LABEL", "notre dernière réunion des RRI")[:140]


def focus_question() -> str:
    return setting("QUESTION_CIBLEE", "Quel sujet devrions-nous traiter en priorité à la prochaine réunion du réseau des RRI, et pourquoi ?")[:380]


def setup_session():
    s = st.session_state
    s.setdefault("history", [{"role": "assistant", "content": FIRST_QUESTION}])
    s.setdefault("phase", "dialogue")
    s.setdefault("priority_asked", False)
    s.setdefault("summary", None)
    s.setdefault("summary_notice", "")
    s.setdefault("last_error", None)
    s.setdefault("last_audio", None)
    s.setdefault("error_details", "")


def reset_conversation():
    for key in list(st.session_state.keys()):
        if key in ("history", "phase", "priority_asked", "summary", "summary_notice", "last_error",
                   "last_audio", "error_details", "editable_summary", "rid_microphone"):
            del st.session_state[key]
    st.rerun()


def user_turns() -> int:
    return sum(m["role"] == "user" for m in st.session_state.history)


def history_context(limit: int = 12) -> list[dict[str, str]]:
    return [{"role": m["role"], "content": m["content"][:800]}
            for m in st.session_state.history[-limit:]]


def prompt_dialogue() -> str:
    return f"""Tu es l'assistant d'écoute de RID (Résultats et Impacts de Développement), équipe de la direction exécutive SDD (Solutions de Développement Durable) de l'AFD (Agence Française de Développement).
Tu échanges avec un RRI (Responsable de Résultats et d'Impacts), correspondant de RID dans l'une des divisions techniques de SDD.
Sujet : {meeting_name()}. Mission : comprendre les réactions des RRI, leurs attentes, irritants et propositions.
Écoute sans défendre RID. Ne demande pas de noms, divisions, projets, cas RH ou informations sensibles.
Une seule question orale à la fois, courte (maximum deux phrases). Pose des relances concrètes, pas un questionnaire rigide.
Question prioritaire : {focus_question()}
Elle est posée à part par l'application, ne la répète pas si déjà posée.
Nombre de réponses de ce RRI : {user_turns()}.
Tu décides quand il semble pertinent de terminer, généralement après quatre ou cinq réponses mais parfois plus tôt s'il le souhaite. Avant de terminer, tu dois demander explicitement si le RRI souhaite ajouter quelque chose ou si l'entretien peut se clore.
Réponds uniquement par un objet JSON valide, sans markdown, avec exactement les champs :
{{"reply":"une relance courte ou une demande explicite de confirmation de fin", "action":"continue"}}
Le champ action peut être "continue" ou "confirm_end". Choisis confirm_end seulement si tu souhaites demander à ce RRI si l'entretien peut se terminer, jamais pour annoncer la synthèse.
"""


def prompt_summary() -> str:
    return """Rédige en français une synthèse brève, strictement fondée sur les propos fournis.
Maximum 850 caractères. Exactement ces rubriques :
Ce que je retiens de la réunion :
Ce qui me serait plus utile / irritants :
Priorité pour la prochaine réunion :
Ne déduis pas d'émotions, n'invente aucun fait. Écarte les noms propres, divisions et détails identifiants.
Si une rubrique est absente, écris « Non exprimé ». Pas de formule de politesse."""


def ask_albert(messages, max_tokens=220):
    return chat(setting("ALBERT_API_KEY"), messages,
                model=setting("ALBERT_CHAT_MODEL", DEFAULT_CHAT_MODEL), max_tokens=max_tokens)


def _parse_dialogue_json(raw: str) -> tuple[str, str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", cleaned, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return cleaned[:450], "continue"
        else:
            return cleaned[:450], "continue"
    if not isinstance(data, dict):
        return "Peux-tu m'en dire un peu plus ?", "continue"
    text = str(data.get("reply", "Peux-tu m'en dire un peu plus ?")).strip()[:450]
    action = data.get("action", "continue")
    return text or "Peux-tu m'en dire un peu plus ?", action if action in ("continue", "confirm_end") else "continue"


def confirm_question() -> str:
    return "Merci. Avant de terminer, est-ce que tu souhaites ajouter quelque chose ou pouvons-nous conclure l'entretien ?"


def confirmation_is_yes(text: str) -> bool:
    text = text.strip().lower()
    return bool(re.search(r"^(oui|ok\b|d.accord|on peut (terminer|conclure)|c.est (bon|tout)|terminons|je n.ai (rien|pas) (d.autre|de plus)|rien (d.autre|à ajouter)|non[, .]*(rien|c.est|je n.ai)|pas (d.autre|de plus)|tout est dit|merci c.est tout)", text))


def confirmation_is_no(text: str) -> bool:
    text = text.strip().lower()
    return bool(re.search(r"^(non (je veux|pas encore|attends)|attends|j.ai (encore|aussi)|je voudrais (ajouter|préciser)|encore (un|une)|pas encore)", text))


def prepare_summary():
    """Une requête courte ; si Albert échoue, brouillon local éditable (pas de 2e coût API)."""
    s = st.session_state
    user_texts = [m["content"] for m in s.history if m["role"] == "user"]
    context = "\n".join(f"Contribution {i+1} : {v[:600]}" for i, v in enumerate(user_texts[-7:]))
    try:
        result = ask_albert([{"role":"system","content":prompt_summary()},
                             {"role":"user","content":context}], max_tokens=300)
        s.summary = result[:MAX_SUMMARY_CHARS]
        s.summary_notice = ""
    except AlbertError as exc:
        s.summary_notice = (f"La synthèse automatique n'a pas abouti ({exc}). "
                            "Vos réponses sont conservées dans cette session ; voici un brouillon de secours à modifier.")
        # Brouillon de secours explicite, sans prétendre à une anonymisation automatique.
        snippets = "\n".join(f"- {v[:220]}" for v in user_texts[-4:])
        s.summary = ("Ce que je retiens de la réunion :\nÀ compléter.\n\n"
                     "Ce qui me serait plus utile / irritants :\nÀ compléter.\n\n"
                     "Priorité pour la prochaine réunion :\nÀ compléter.\n\n"
                     "Mes propos à reformuler et dépersonnaliser avant envoi :\n" + snippets)[:MAX_SUMMARY_CHARS]
    s.editable_summary = s.summary
    s.phase = "done"


def respond(text: str):
    s = st.session_state
    cleaned = text.strip()[:MAX_TEXT_CHARS]
    if not cleaned or s.phase == "done":
        return
    s.last_error = None
    s.history.append({"role": "user", "content": cleaned})
    n = user_turns()
    if s.phase == "confirmation":
        if confirmation_is_yes(cleaned):
            prepare_summary()
            return
        if not confirmation_is_no(cleaned):
            # Un propos supplémentaire doit être exploré plutôt que considéré comme une validation.
            if len(cleaned) <= 50 and ("oui" in cleaned.lower() or "termin" in cleaned.lower()):
                prepare_summary()
                return
        s.phase = "dialogue"
    if n >= 3 and not s.priority_asked:
        s.priority_asked = True
        s.history.append({"role": "assistant", "content": focus_question()})
        return
    if n >= MAX_DIALOGUE_TURNS:
        s.phase = "confirmation"
        s.history.append({"role": "assistant", "content": confirm_question()})
        return
    try:
        text_reply, action = _parse_dialogue_json(ask_albert(
            [{"role": "system", "content": prompt_dialogue()}] + history_context(), max_tokens=230))
        if action == "confirm_end":
            s.phase = "confirmation"
            # Forcer une vraie confirmation avant tout résumé.
            if not any(w in text_reply.lower() for w in ("termin", "conclu", "ajouter", "autre chose", "fini")):
                text_reply = confirm_question()
        s.history.append({"role": "assistant", "content": text_reply})
    except AlbertError as exc:
        s.last_error = str(exc)
        s.error_details = f"HTTP {exc.status or 'réseau'} · {exc.category}"
        # Ne pas perdre la réponse : poser une relance utilisable sans nouvel appel.
        if n >= 4:
            s.phase = "confirmation"
            fallback = confirm_question()
        else:
            fallback = "Merci pour ce retour. Quel changement concret te serait le plus utile dans le réseau des RRI ?"
        s.history.append({"role":"assistant", "content":fallback})


def mailto_link(recipient: str, summary: str) -> str:
    subject = f"Retour RRI – {meeting_name()}"
    body = ("Bonjour l'équipe RID,\n\nVoici mon retour, relu et validé avant envoi :\n\n"
            + summary + "\n\n")
    return "mailto:" + quote(recipient, safe="@+.-") + "?subject=" + quote(subject, safe="") + "&body=" + quote(body, safe="")


# ===== Écran principal =====
st.title("Le robavardeur de chez RID")
st.caption("Réseau des RRI · Direction exécutive SDD · AFD")

needed = ("ALBERT_API_KEY", "RESULT_EMAIL")
missing = [v for v in needed if not setting(v)]
if missing:
    st.error("Configuration manquante : " + ", ".join(missing))
    st.stop()

close_date = setting("OPEN_UNTIL_UTC")
if close_date:
    try:
        closing = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
        if closing.tzinfo is None:
            raise ValueError("Timezone")
    except ValueError:
        st.error("Date OPEN_UNTIL_UTC mal configurée (ISO 8601 avec fuseau).")
        st.stop()
    if datetime.now(timezone.utc) > closing:
        st.info("Cette campagne d'écoute est terminée.")
        st.stop()

setup_session()
with st.expander("Confidentialité et utilisation — à lire avant de commencer", expanded=False):
    st.markdown("""Tu échanges avec un agent IA utilisant **Albert API**, dans le cadre d'un test organisé par
l'équipe **RID** auprès des **RRI** des divisions techniques de **SDD (AFD)**.
L'échange est facultatif. Évite les noms, dossiers sensibles et détails identifiants.
Le service Streamlit héberge l'application et transmet temporairement l'audio à Albert pour transcription ;
les fournisseurs peuvent conserver des journaux techniques selon leurs conditions.
Aucune conversation n'est sauvegardée dans une base de données de cette application.
**L'e-mail final est identifiable par son expéditeur**, même si seules des tendances collectives seront
restituées au réseau. Tu pourras corriger la synthèse avant de l'envoyer ou fermer sans rien envoyer.
La première activation vocale demande l'autorisation du microphone ; les suivantes sont automatisées
lorsque le navigateur le permet.""")

c1, c2 = st.columns([2,1])
with c1:
    st.caption(f"Réunion : {meeting_name()}")
with c2:
    if st.button("Effacer l'échange"):
        reset_conversation()

s = st.session_state
if s.phase != "done":
    # L'interface met l'accent sur le dernier tour ; historique facultatif.
    latest = s.history[-1]
    if latest["role"] != "assistant":
        s.history.append({"role": "assistant", "content": confirm_question()})
        latest = s.history[-1]
    question_id = hashlib.sha256((str(len(s.history)) + latest["content"]).encode()).hexdigest()[:16]
    st.markdown(f"**Échange {user_turns()+1}** · environ 5 minutes au total")
    if user_turns() == 0:
        st.caption("En cliquant sur « Démarrer », vous acceptez les conditions présentées dans l’encadré de confidentialité. Le navigateur peut demander l’autorisation d’utiliser le micro.")
    voice = voice_turn(latest["content"], question_id, auto_start=(user_turns()>0))
    if voice.audio:
        event = voice.audio
        if isinstance(event, dict) and event.get("turn_id") == question_id and isinstance(event.get("wav_base64"), str):
            raw = event["wav_base64"]
            if len(raw) > 10_000_000:
                st.error("Audio trop volumineux. Répondez en texte ou réessayez.")
            else:
                try:
                    payload = base64.b64decode(raw, validate=True)
                    with st.spinner("Albert transcrit la réponse…"):
                        transcript = transcribe(setting("ALBERT_API_KEY"), payload,
                                                model=setting("ALBERT_ASR_MODEL", DEFAULT_ASR_MODEL))
                    with st.spinner("Albert prépare sa relance…"):
                        respond(transcript)
                    st.rerun()
                except (AlbertError, ValueError) as exc:
                    st.error(f"Problème de transcription : {exc}")
                    st.info("Vous pouvez utiliser le clavier ci-dessous sans perdre votre échange.")
    elif voice.finish_request:
        event = voice.finish_request
        if isinstance(event, dict) and event.get("turn_id") == question_id:
            s.phase = "confirmation"
            s.history.append({"role":"assistant", "content": confirm_question()})
            st.rerun()

    if s.last_error:
        st.warning(f"Albert n'a pas pu générer la dernière relance ({s.error_details}). "
                   "Une question de secours a été proposée. Vous pouvez continuer.")
    with st.expander("Répondre par écrit à la place", expanded=False):
        with st.form("text_fallback", clear_on_submit=True):
            answer = st.text_area("Ma réponse", max_chars=MAX_TEXT_CHARS)
            submitted = st.form_submit_button("Envoyer ma réponse")
        if submitted and answer.strip():
            with st.spinner("Albert prépare sa relance…"):
                respond(answer)
            st.rerun()
    with st.expander("Relire l'échange", expanded=False):
        for m in s.history:
            st.markdown(("**RRI :** " if m["role"] == "user" else "**Assistant :** ") +
                        html.escape(m["content"]))
else:
    st.success("L'entretien est terminé. Voici la synthèse à vérifier avant tout envoi.")
    if s.summary_notice:
        st.warning(s.summary_notice)
    st.text_area("Synthèse modifiable", key="editable_summary", height=240,
                 max_chars=MAX_SUMMARY_CHARS)
    summary = s.editable_summary.strip()
    st.caption("Effacez toute information permettant d'identifier un collègue ou une division. "
               "Le message partira depuis votre propre messagerie et ne sera pas anonyme.")
    if summary:
        url = html.escape(mailto_link(setting("RESULT_EMAIL"), summary), quote=True)
        st.markdown(f'<a href="{url}" target="_self" style="display:inline-block;background:#12649b;'
                    'color:white;border-radius:10px;text-decoration:none;padding:11px 16px;font-weight:600">'
                    'Ouvrir mon e-mail de restitution</a>', unsafe_allow_html=True)
        st.code(summary, language=None)
        st.caption("Si votre navigateur ne sait pas ouvrir Outlook à partir d'un lien mailto, "
                   "copiez la synthèse et collez-la dans un nouveau message.")
    if st.button("Nouvel entretien"):
        reset_conversation()
