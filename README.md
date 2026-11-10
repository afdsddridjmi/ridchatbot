# La voix des correspondants — pilote vocal Albert / Streamlit

**Prototype à 0 € d'hébergement**, sans machine virtuelle, sans base de données, sans clé API exposée au navigateur. Le coût zéro dépend de vos droits/quota Albert et de la disponibilité de l'offre gratuite Streamlit.

## Ce que voit le correspondant

1. Il ouvre le lien Streamlit reçu par Outlook et saisit un code commun.
2. Il accepte les informations de traitement des données, puis voit une question.
3. Il clique sur **Écouter la question** pour l'entendre dans la voix de son navigateur (ou la lit).
4. Il clique sur **Enregistrer**, parle, puis arrête l'enregistrement. **La transcription démarre automatiquement**, puis Albert rédige une relance adaptée.
5. Il répète jusqu'à 7 interventions au maximum, ou termine avant.
6. Albert propose une synthèse en trois rubriques. Le participant la corrige, puis clique sur **Ouvrir un brouillon d'e-mail** ; il choisit ou non d'envoyer le message.

**Important : il ne s'agit pas d'un appel vocal temps réel.** Le participant démarre et arrête chaque prise de parole et clique pour entendre la relance. La synthèse vocale dépend des voix disponibles dans son navigateur et de ses réglages. Une variante écrite existe en secours.

## Mise en ligne, sans rien installer localement

1. Rendez-vous sur **https://github.com/new**. Créez un dépôt privé, p. ex. `ecoute-correspondants-albert`.
2. Dans ce dépôt, cliquez sur **Add file → Upload files**, glissez **`app.py`, `albert_api.py`, `requirements.txt`, `.gitignore`, `secrets.example.toml` et le sous-dossier `.streamlit/config.toml`** (ou tous les fichiers du ZIP décompressé), puis validez **Commit changes**. Ne téléversez jamais un vrai secret.
3. Ouvrez **https://share.streamlit.io**, connectez votre GitHub puis cliquez **Create app**. Sélectionnez le dépôt, la branche principale et le fichier `app.py`.
4. Cliquez **Advanced settings → Secrets** et collez le contenu de `secrets.example.toml`, avec quatre valeurs remplacées :
   - `ALBERT_API_KEY` : clé autorisée à utiliser les modèles de *chat* **et** de *transcription*.
   - `ACCESS_CODE` : longue chaîne aléatoire commune aux testeurs (16 caractères ou davantage).
   - `RESULT_EMAIL` : adresse qui recevra **les e-mails envoyés manuellement** par les correspondants.
   - `OPEN_UNTIL_UTC` : date/heure de fermeture du pilote (UTC, avec `+00:00`).
5. **Deploy** : vous recevrez un lien `https://...streamlit.app` à diffuser dans Outlook avec le code. Testez auparavant une conversation fictive sur ordinateur et sur le téléphone, avec votre navigateur d'entreprise.

Les modèles par défaut, issus de la publication DINUM, sont :
- Chat : `mistralai/Mistral-Small-3.2-24B-Instruct-2506`.
- Transcription : `openai/whisper-large-v3`.

**Si erreur “Modèle Albert indisponible”** : connectez-vous à votre espace Albert, consultez les modèles auxquels votre clé accède via `GET https://albert.api.etalab.gouv.fr/v1/models`, et définissez si besoin `ALBERT_CHAT_MODEL` et `ALBERT_ASR_MODEL` dans les Secrets Streamlit. Il est possible que vos clés ne donnent pas accès à la transcription : dans ce cas, le vocal ne fonctionnera pas avec ces droits, même si le texte fonctionne.

## Tester avant de diffuser

- Depuis le navigateur, ouvrir le lien sur un ordinateur et cliquer sur le bouton d'écoute.
- Enregistrer une phrase, arrêter : la transcription doit démarrer automatiquement. Vérifier la fidélité du texte et la pertinence de la relance.
- Recommencer deux ou trois fois ; vérifier la question prioritaire.
- Terminer, modifier la synthèse et cliquer sur le brouillon e-mail : **aucun e-mail ne doit partir sans clic sur Envoyer dans Outlook**.
- Cliquer sur **Effacer l'échange** et vérifier que le texte n'apparaît plus.
- Tester le mode écrit, notamment sur mobile ou un poste sans microphone.

## Confidentialité et limites du prototype

- **L'application n'enregistre aucun fichier ni base de conversations.** Les textes restent en mémoire de session côté serveur jusqu'à l'effacement, la déconnexion ou la fin de vie de la session ; les clips audio sont reçus temporairement par le serveur Streamlit. Les prestataires (Streamlit, Albert, potentiellement la synthèse vocale du navigateur) peuvent effectuer leurs propres traitements techniques ou journalisations : ce prototype ne permet pas de leur imposer un effacement complet.
- Streamlit Community Cloud est un hébergement externe : **validation DPO/sécurité nécessaire avant tout retour réel en milieu professionnel**, même si Albert est un service autorisé chez vous.
- **Le brouillon envoyé via Outlook identifie l'expéditeur.** Le pilote ne garantit PAS l'anonymat vis-à-vis des animateurs ; seule une restitution collective au réseau est envisagée.
- **Le code partagé n'est pas une authentification forte**. Un lien/code transféré peut entraîner des appels au quota Albert. Limites locales : 7 réponses par conversation, 90 secondes et 8 Mo par audio ; aucune limite globale centralisée. Un budget/quota côté Albert est recommandé. Révoquez la clé dédiée à la fin.
- Ne collectez ni noms, ni divisions, ni détails de dossiers, ni données RH sensibles. Le modèle peut faire des erreurs : la relecture de la synthèse est obligatoire.
- Le navigateur doit autoriser le micro ; certaines politiques d'entreprise bloquent le microphone ou l'accès à `streamlit.app`. Vérifier ces deux points d'abord.
- Le navigateur peut utiliser une voix de synthèse locale **ou** un moteur associé à un service distant. N'en déduisez pas une confidentialité absolue de la restitution vocale.
- `mailto:` utilise le logiciel de courrier par défaut, pas toujours Outlook. La synthèse peut être copiée manuellement si nécessaire.
- Les sessions Streamlit ne sont pas persistantes : un rechargement ou une déconnexion peut effacer le travail sans envoyer de mail.

## Comment personnaliser les réunions

Dans les *Secrets*, définissez au besoin :

```toml
MEETING_LABEL = "la réunion du 12 novembre 2026"
QUESTION_CIBLEE = "Quel sujet doit devenir notre priorité la prochaine fois, et pourquoi ?"
```

## Références officielles

- Albert API : https://albert.api.etalab.gouv.fr/reference
- Tarifs/quotas Albert : https://ia.numerique.gouv.fr/outils-ia/albert-api/tarifs-et-limites/
- Streamlit `st.audio_input` : https://docs.streamlit.io/develop/api-reference/widgets/st.audio_input
- Streamlit Community Cloud : https://docs.streamlit.io/deploy/streamlit-community-cloud
- Secrets Streamlit : https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
