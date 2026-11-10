"""L'Oreille de RID : entretien vocal, collecte volontaire des RRI de SDD/AFD.

V3.0 : démarrage audio tenté au chargement, transcription visible, clôture fiable.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import streamlit as st
from albert_api import AlbertError, DEFAULT_ASR_MODEL, DEFAULT_CHAT_MODEL, chat, transcribe
from voice_ui import voice_turn

st.set_page_config(page_title="Dis-le à l'Oreille de RID", page_icon="🎙️", layout="centered")
MAX_DIALOGUE_TURNS = 7
MAX_TEXT_CHARS = 1600
MAX_SUMMARY_CHARS = 1250
FIRST_QUESTION = "Bonjour ! Qu'est-ce que tu retiens de notre dernière réunion des RRI ?"
CONFIRM_QUESTION = "Merci pour tes retours. Est-ce qu'on peut conclure l'entretien ? Si tu as un dernier point, je t'écoute."


def setting(name: str, default: str = "") -> str:
    return str(st.secrets.get(name, default)).strip()


def meeting_name() -> str:
    return setting("MEETING_LABEL", "notre dernière réunion des RRI")[:140]


def focus_question() -> str:
    return setting("QUESTION_CIBLEE", "Quel sujet devrions-nous traiter en priorité à la prochaine réunion des RRI, et pourquoi ?")[:380]


def setup_session():
    s = st.session_state
    s.setdefault("conversation_id", uuid.uuid4().hex)
    s.setdefault("history", [{"role": "assistant", "content": FIRST_QUESTION}])
    s.setdefault("phase", "dialogue")
    s.setdefault("priority_asked", False)
    s.setdefault("summary", None)
    s.setdefault("summary_notice", "")
    s.setdefault("last_error", None)
    s.setdefault("error_details", "")


def reset_conversation():
    for key in ["conversation_id", "history", "phase", "priority_asked", "summary", "summary_notice", "last_error",
                "error_details", "editable_summary", "rid_microphone"]:
        st.session_state.pop(key, None)
    st.rerun()


def user_turns() -> int:
    return sum(m["role"] == "user" for m in st.session_state.history)


def history_context(limit: int = 12) -> list[dict[str, str]]:
    return [{"role": m["role"], "content": m["content"][:800]}
            for m in st.session_state.history[-limit:]]


def prompt_dialogue() -> str:
    return f"""Tu es « L'Oreille de RID », agent d'écoute de l'équipe RID (Résultats et Impacts de Développement), direction exécutive SDD (Solutions de Développement Durable) de l'AFD (Agence Française de Développement).
Tu parles à un RRI (Responsable de Résultats et d'Impacts), relais de RID dans l'une des divisions techniques de SDD.
Sujet : {meeting_name()}. Écoute ses réactions, attentes, irritants et idées, sans défendre RID.
Ne demande pas de noms, division, dossiers sensibles, situations RH ou autres détails identifiants.
Sois bref, naturel, une seule question orale à la fois. Pose des relances concrètes, pas de questionnaire rigide.
Question prioritaire (posée séparément par l'application) : {focus_question()}.
Elle est posée par l'application : ne la répète pas si elle a déjà été posée.
Nombre de réponses du RRI : {user_turns()}. Choisis de proposer une conclusion après quelques réponses, si cela semble naturel.
Si tu considères l'entretien assez complet, choisis l'action confirm_end. Ne pose PAS toi-même la question de clôture : l'application l'imposera pour éviter les doublons.
Réponds uniquement avec un objet JSON valide, sans markdown, de la forme :
{{"reply":"une question de relance courte", "action":"continue"}}
Action doit valoir « continue » ou « confirm_end ». Pour confirm_end, reply peut être vide.
"""


def prompt_summary() -> str:
    return """Rédige en français une synthèse courte, exclusivement fondée sur les propos du RRI ci-dessous.
Maximum 850 caractères. Utilise exactement ces rubriques :
Ce que je retiens de la réunion :
Ce qui me serait plus utile / irritants :
Priorité pour la prochaine réunion :
N'invente rien. Supprime les noms propres, divisions, projets ou détails identifiants. Une rubrique absente vaut « Non exprimé ».
Pas de formule de politesse."""


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
    text = str(data.get("reply") or "").strip()[:450]
    action = data.get("action", "continue")
    if action == "confirm_end":
        return "", "confirm_end"
    return text or "Peux-tu m'en dire un peu plus ?", "continue"


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", s)).strip()


def confirmation_is_no(text: str) -> bool:
    t = normalise(text)
    return bool(re.search(r"\b(pas encore|ne (veux|souhaite) pas (finir|terminer|conclure)|encore (un|une|quelque chose)|je (veux|souhaite|voudrais|vais) (ajouter|preciser|completer|revenir)|attends|non je veux|non je souhaite|pas tout a fait)\b", t))


def confirmation_is_yes(text: str) -> bool:
    t = normalise(text)
    if confirmation_is_no(text):
        return False
    if t in {"oui", "ok", "d accord", "daccord", "c est bon", "c est tout", "merci", "terminons", "on conclut", "vas y", "oui merci", "non rien", "non merci"}:
        return True
    if re.match(r"^(oui|ok|d accord|daccord)\b", t) and not re.search(r"\b(mais|cependant|pourtant)\b", t):
        return True
    return bool(re.search(r"\b(on peut|nous pouvons|je souhaite|je veux|je voudrais|tu peux|vous pouvez|ok pour|pret a|on va)\s+(?:bien\s+)?(?:conclure|terminer|finir|clore)\b|\b(?:rien|plus rien|pas grand chose)\s+(?:d autre|a ajouter|de plus)\b|\b(?:j ai termine|j ai fini|c est tout|tout est dit|on peut s arreter|c est bon pour moi|pas d autre point|je n ai rien a ajouter)\b", t))


def confirm_question() -> str:
    return CONFIRM_QUESTION


def request_finish():
    """Bouton de clôture : après la confirmation, terminer sans faire intervenir le modèle."""
    s = st.session_state
    if s.phase == "done":
        return
    if s.phase == "confirmation":
        prepare_summary()
    else:
        s.phase = "confirmation"
        s.history.append({"role": "assistant", "content": confirm_question()})


def prepare_summary():
    """Synthèse + secours local si l'API refuse une requête."""
    s = st.session_state
    if s.phase == "done":
        return
    user_texts = [m["content"] for m in s.history if m["role"] == "user"]
    if not user_texts:
        s.summary = ("Ce que je retiens de la réunion :\nNon exprimé.\n\n"
                     "Ce qui me serait plus utile / irritants :\nNon exprimé.\n\n"
                     "Priorité pour la prochaine réunion :\nNon exprimé.")
        s.summary_notice = ""
        s.editable_summary = s.summary
        s.phase = "done"
        return
    context = "\n".join(f"Contribution {i+1} : {v[:600]}" for i, v in enumerate(user_texts[-7:]))
    try:
        result = ask_albert([{"role": "system", "content": prompt_summary()},
                             {"role": "user", "content": context}], max_tokens=300)
        s.summary = result[:MAX_SUMMARY_CHARS]
        s.summary_notice = ""
    except AlbertError as exc:
        s.summary_notice = (f"La synthèse automatique n'a pas abouti ({exc}). "
                            "Voici un brouillon de secours ; relisez et supprimez les détails identifiants.")
        snippets = "\n".join(f"- {v[:180]}" for v in user_texts[-4:])
        s.summary = ("Ce que je retiens de la réunion :\nÀ compléter.\n\n"
                     "Ce qui me serait plus utile / irritants :\nÀ compléter.\n\n"
                     "Priorité pour la prochaine réunion :\nÀ compléter.\n\n"
                     "À reformuler et dépersonnaliser :\n" + snippets)[:MAX_SUMMARY_CHARS]
    s.editable_summary = s.summary
    s.phase = "done"


def respond(text: str):
    """Contrôle d'état local. Albert ne peut pas empêcher la clôture."""
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
        if confirmation_is_no(cleaned):
            s.phase = "dialogue"
            s.history.append({"role": "assistant", "content": "Bien sûr. Quel dernier point souhaites-tu développer ?"})
            return
        # Contribution supplémentaire : conserver puis redemander explicitement la confirmation.
        s.history.append({"role": "assistant", "content": "Merci pour cette précision. Est-ce qu'on peut maintenant conclure l'entretien ?"})
        return

    if confirmation_is_yes(cleaned) and re.search(r"\b(conclure|terminer|finir|clore|arreter)\b", normalise(cleaned)):
        request_finish()
        return

    if n >= 3 and not s.priority_asked:
        s.priority_asked = True
        s.history.append({"role": "assistant", "content": focus_question()})
        return

    if n >= MAX_DIALOGUE_TURNS:
        request_finish()
        return

    try:
        reply, action = _parse_dialogue_json(ask_albert(
            [{"role": "system", "content": prompt_dialogue()}] + history_context(), max_tokens=230))
        if action == "confirm_end":
            request_finish()
        else:
            s.history.append({"role": "assistant", "content": reply})
    except AlbertError as exc:
        s.last_error = str(exc)
        s.error_details = f"HTTP {exc.status or 'réseau'} · {exc.category}"
        if n >= 4:
            request_finish()
        else:
            s.history.append({"role": "assistant", "content": "Merci. Quel changement concret te serait le plus utile pour la suite ?"})


def mailto_link(recipient: str, summary: str) -> str:
    subject = f"Retour RRI – {meeting_name()}"
    body = "Bonjour l'équipe RID,\n\nVoici mon retour, relu et validé avant envoi :\n\n" + summary + "\n\n"
    return "mailto:" + quote(recipient, safe="@+.-") + "?subject=" + quote(subject, safe="") + "&body=" + quote(body, safe="")


st.markdown("""<style>
/* Dock mobile/desktop : transcript interne défilant, contrôles + repli écrit visibles en bas. */
.st-key-rid_conversation_dock {position:sticky; bottom:0; z-index:15;
  background:var(--background-color, #fff); padding:.35rem .25rem .6rem;
  border-radius:14px; box-shadow:0 -5px 20px rgba(0,0,0,.09);
  max-height:79dvh; overflow-y:auto; overscroll-behavior:contain;}
.st-key-rid_conversation_dock [data-testid="stExpander"] {margin-top:.15rem;}
@media(max-height:620px) {.st-key-rid_conversation_dock {max-height:86dvh;}}
</style>""", unsafe_allow_html=True)

st.title("Dis-le à l'Oreille de RID")
st.caption("L'Oreille de RID · RRI des divisions techniques · RID / SDD / AFD")

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
with st.expander("Confidentialité et utilisation", expanded=False):
    st.markdown("""Tu discutes avec **L'Oreille de RID**. L'Oreille de RID est une sorte de ChatGPT simplifié :
un agent conversationnel (chatbot) de l'équipe **RID (AFD)** qui s'appuie sur le service d'intelligence
artificielle **Albert API**, mis à disposition de l'AFD par la **Direction interministérielle du numérique (DINUM)**.

La discussion avec l'Oreille est **facultative**. Le moteur d'IA Albert est conçu pour des usages professionnels
sécurisés, mais tous les maillons de ce logiciel n'offrent pas nécessairement le même niveau de protection.
**Évite donc de parler de données personnelles** (par exemple les noms de famille des collègues),
**confidentielles ou bancaires** (par exemple les tarifications, les résultats financiers ou les confidences
des contreparties).

L'Oreille t'aide simplement à **rédiger un e-mail final que tu pourras modifier et envoyer — ou non — à RID**.
**RID ne reçoit pas automatiquement le contenu de ta conversation.** Aucune base de données de cette
application ne conserve les échanges, mais ils sont traités temporairement pendant ta session par
**Streamlit et Albert** ; ces services peuvent conserver des journaux techniques selon leurs conditions.
Si tu fermes ou recharges la page, **ton échange en cours peut être perdu**. Si tu envoies l'e-mail,
RID pourra identifier son expéditeur.

Lors de la première utilisation vocale, ton navigateur peut demander l'autorisation d'utiliser le microphone.
**Si ton navigateur propose de mémoriser cette autorisation**, tu peux sélectionner cette option pour éviter
d'avoir à l'accorder à nouveau à chaque visite.""")

col_left, col_right = st.columns([3, 1])
with col_left:
    st.caption(f"Réunion : {meeting_name()}")
with col_right:
    if st.button("Effacer", help="Effacer cet échange et recommencer"):
        reset_conversation()

s = st.session_state
if s.phase != "done":
    latest = s.history[-1]
    if latest["role"] != "assistant":
        # Garde-fou sans synthèse implicite ni nouvelle boucle de clôture.
        s.phase = "confirmation"
        s.history.append({"role": "assistant", "content": confirm_question()})
        latest = s.history[-1]
    question_id = hashlib.sha256((s.conversation_id + str(len(s.history)) + latest["content"]).encode()).hexdigest()[:16]

    with st.container(key="rid_conversation_dock"):
        voice = voice_turn(latest["content"], question_id, history=s.history,
                           is_confirmation=(s.phase == "confirmation"))
        if voice.audio:
            event = voice.audio
            if isinstance(event, dict) and event.get("turn_id") == question_id and isinstance(event.get("wav_base64"), str):
                raw = event["wav_base64"]
                if len(raw) > 10_000_000:
                    st.error("Audio trop volumineux. Utilisez la réponse écrite ou réessayez.")
                else:
                    try:
                        payload = base64.b64decode(raw, validate=True)
                        with st.spinner("L'Oreille de RID transcrit votre réponse…"):
                            transcript = transcribe(setting("ALBERT_API_KEY"), payload,
                                                    model=setting("ALBERT_ASR_MODEL", DEFAULT_ASR_MODEL))
                        with st.spinner("L'Oreille de RID prépare sa relance…"):
                            respond(transcript)
                        st.rerun()
                    except (AlbertError, ValueError) as exc:
                        st.error(f"Problème de transcription : {exc}")
                        st.info("Vous pouvez utiliser le clavier sans perdre votre échange.")
        elif voice.finish_request:
            event = voice.finish_request
            if isinstance(event, dict) and event.get("turn_id") == question_id:
                with st.spinner("Préparation de la synthèse…"):
                    request_finish()
                st.rerun()

        with st.expander("Répondre par écrit à la place", expanded=False):
            with st.form("text_fallback", clear_on_submit=True):
                answer = st.text_area("Ma réponse", max_chars=MAX_TEXT_CHARS)
                submitted = st.form_submit_button("Envoyer ma réponse")
            if submitted and answer.strip():
                with st.spinner("L'Oreille de RID prépare sa relance…"):
                    respond(answer)
                st.rerun()

    if s.last_error:
        st.warning("La dernière relance automatique n'a pas abouti "
                   f"({s.error_details}). Une question de secours a été proposée.")
else:
    st.success("Entretien terminé. Voici le récapitulatif à relire avant tout envoi.")
    if s.summary_notice:
        st.warning(s.summary_notice)
    st.text_area("Synthèse modifiable", key="editable_summary", height=240,
                 max_chars=MAX_SUMMARY_CHARS)
    summary = s.editable_summary.strip()
    st.caption("Supprimez toute information permettant d'identifier une personne ou une division. "
               "L'e-mail final ne sera pas anonyme vis-à-vis de RID.")
    if summary:
        url = html.escape(mailto_link(setting("RESULT_EMAIL"), summary), quote=True)
        st.markdown(f'<a href="{url}" target="_self" style="display:inline-block;background:#12649b;'
                    'color:white;border-radius:10px;text-decoration:none;padding:11px 16px;font-weight:600">'
                    'Ouvrir mon e-mail de restitution</a>', unsafe_allow_html=True)
        st.code(summary, language=None)
        st.caption("Si l'e-mail ne s'ouvre pas automatiquement dans Outlook, copiez le récapitulatif et collez-le dans un nouveau message.")
    if st.button("Nouvel entretien"):
        reset_conversation()
