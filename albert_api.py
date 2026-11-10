"""Client minimal Albert API. Aucun journal de requête, aucune écriture sur disque."""
from __future__ import annotations

import io
import wave

import requests

BASE_URL = "https://albert.api.etalab.gouv.fr/v1"
DEFAULT_CHAT_MODEL = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
DEFAULT_ASR_MODEL = "openai/whisper-large-v3"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_DURATION = 90  # secondes par réponse


class AlbertError(RuntimeError):
    """Erreur affichable à l'utilisateur sans divulgation de secrets ni de données."""


def check_wav(data: bytes) -> float:
    """Vérifie le WAV et sa durée (les données restent en mémoire)."""
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise AlbertError("Enregistrement vide ou trop volumineux (maximum 8 Mo).")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            channels = audio.getnchannels()
    except (wave.Error, EOFError, ZeroDivisionError) as exc:
        raise AlbertError("Impossible de lire cet enregistrement WAV. Réessayez.") from exc
    if duration < 0.5:
        raise AlbertError("Enregistrement trop court. Essayez de parler un peu plus longtemps.")
    if duration > MAX_AUDIO_DURATION:
        raise AlbertError("Réponse trop longue (90 secondes au maximum par prise de parole).")
    if channels > 2:
        raise AlbertError("Format audio inattendu : enregistrez votre voix normalement.")
    return duration


def _check_http(response: requests.Response) -> None:
    if response.status_code in (401, 403):
        raise AlbertError("Clé Albert invalide ou accès insuffisant. Contactez l'organisateur.")
    if response.status_code == 400:
        raise AlbertError("Albert refuse la requête (budget ou paramètres). Contactez l'organisateur.")
    if response.status_code in (404, 422):
        raise AlbertError("Modèle Albert indisponible pour cette clé. Vérifiez les modèles configurés.")
    if response.status_code == 413:
        raise AlbertError("Fichier audio refusé : taille excessive.")
    if response.status_code == 429:
        raise AlbertError("Quota Albert temporairement atteint. Réessayez plus tard.")
    if response.status_code >= 400:
        raise AlbertError(f"Albert est indisponible (code HTTP {response.status_code}).")


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
        _check_http(response)
        transcript = response.json().get("text", "")
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise AlbertError("La transcription a échoué. Réessayez ou utilisez le mode texte.") from exc
    if not isinstance(transcript, str) or not transcript.strip():
        raise AlbertError("Aucune parole n'a été reconnue. Réessayez ou utilisez le mode texte.")
    return transcript.strip()[:1800]


def chat(key: str, messages: list[dict[str, str]], model: str = DEFAULT_CHAT_MODEL,
         max_tokens: int = 200) -> str:
    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False},
            timeout=(10, 70),
        )
        _check_http(response)
        answer = response.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AlbertError("La génération de réponse a échoué. Réessayez.") from exc
    if not isinstance(answer, str) or not answer.strip():
        raise AlbertError("Albert a renvoyé une réponse vide. Réessayez.")
    return answer.strip()
