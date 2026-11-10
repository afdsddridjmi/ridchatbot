"""Client HTTP Albert, conservant la clé et les textes uniquement côté serveur."""
from __future__ import annotations
import io
import logging
import wave
import requests

BASE_URL = "https://albert.api.etalab.gouv.fr/v1"
DEFAULT_CHAT_MODEL = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
DEFAULT_ASR_MODEL = "openai/whisper-large-v3"
MAX_AUDIO_BYTES = 7 * 1024 * 1024
MAX_AUDIO_DURATION = 92
LOGGER = logging.getLogger(__name__)


class AlbertError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, category: str = "unknown"):
        super().__init__(message)
        self.status = status
        self.category = category


def check_wav(data: bytes) -> float:
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise AlbertError("Enregistrement vide ou trop volumineux (maximum 7 Mo).")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getframerate() <= 0:
                raise ValueError("framerate")
            seconds = w.getnframes() / w.getframerate()
            nchannels, bits = w.getnchannels(), w.getsampwidth()
    except (wave.Error, EOFError, ValueError, ZeroDivisionError) as exc:
        raise AlbertError("Enregistrement WAV invalide. Réessayez.") from exc
    if seconds < 0.5 or seconds > MAX_AUDIO_DURATION:
        raise AlbertError("Enregistrement trop court ou trop long (maximum 90 secondes).")
    if nchannels != 1 or bits != 2:
        raise AlbertError("Format audio inattendu (WAV mono 16 bits requis).")
    return seconds


def _check_http(response: requests.Response, endpoint: str = "Albert") -> None:
    status = response.status_code
    if status < 400:
        return
    # On n'affiche jamais une réponse brute du serveur (potentiellement sensible).
    try:
        err = response.json()
        detail = str(err.get("detail", err.get("message", err.get("error", ""))))[:300] if isinstance(err, dict) else ""
    except (ValueError, TypeError):
        detail = ""
    low = detail.lower()
    if status == 400 and "budget" in low:
        category, message = "budget", "Albert HTTP 400 : budget d'API insuffisant. Vérifiez le quota de votre clé."
    elif status == 400 and any(k in low for k in ("token", "context", "parameter", "paramètre", "max_")):
        category, message = "parameters", "Albert HTTP 400 : paramètre ou taille de contexte non accepté."
    elif status == 400:
        category, message = "http400", "Albert HTTP 400 : requête refusée. Consultez les journaux de l'application."
    elif status == 401:
        category, message = "authentication", "Albert HTTP 401 : clé API invalide."
    elif status == 403:
        category, message = "authorization", "Albert HTTP 403 : accès refusé ou compte expiré."
    elif status in (404, 422):
        category, message = "model", f"Albert HTTP {status} : modèle ou paramètres indisponibles pour cette clé."
    elif status == 413:
        category, message = "size", "Albert HTTP 413 : fichier audio trop volumineux."
    elif status == 429:
        category, message = "rate", "Albert HTTP 429 : quota de débit temporairement dépassé."
    elif status in (502, 503, 504):
        category, message = "unavailable", f"Albert HTTP {status} : modèle temporairement indisponible."
    else:
        category, message = "server", f"Albert HTTP {status} : appel refusé."
    # Ne journaliser que le statut et la catégorie : aucun secret ni transcript.
    LOGGER.warning("Appel %s refusé : HTTP %s, catégorie %s", endpoint, status, category)
    raise AlbertError(message, status=status, category=category)


def transcribe(key: str, audio: bytes, model: str = DEFAULT_ASR_MODEL) -> str:
    check_wav(audio)
    try:
        response = requests.post(
            f"{BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("reponse.wav", audio, "audio/wav")},
            data={"model": model, "language": "fr", "response_format": "json"},
            timeout=(10, 95),
        )
        _check_http(response, "transcription")
        transcript = response.json().get("text", "")
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise AlbertError("Échec de transcription ou de réseau. Réessayez, ou écrivez votre réponse.") from exc
    if not isinstance(transcript, str) or not transcript.strip():
        raise AlbertError("Aucune parole reconnue. Réessayez ou utilisez le clavier.")
    return transcript.strip()[:1800]


def chat(key: str, messages: list[dict[str, str]], model: str = DEFAULT_CHAT_MODEL,
         max_tokens: int = 220) -> str:
    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "max_completion_tokens": max_tokens, "stream": False},
            timeout=(10, 70),
        )
        _check_http(response, "chat")
        answer = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AlbertError("Réponse d'Albert non disponible. Réessayez.") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise AlbertError("Albert a retourné une réponse vide.")
    return answer.strip()
