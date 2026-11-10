// Composant Streamlit v2 : voix navigateur + enregistrement WAV via Web Audio.
// Pas de clé Albert dans ce code. Une interaction initiale est obligatoire.
export default function(component) {
  const { parentElement: root, data, setTriggerValue } = component;
  const $ = (id) => root.querySelector('#' + id);
  const startBtn = $('rid-start');
  const replayBtn = $('rid-replay');
  const sendBtn = $('rid-send');
  const finishBtn = $('rid-finish');
  const status = $('rid-status');
  const meter = $('rid-meter');
  const prompt = $('rid-prompt');
  prompt.textContent = data.question;

  let alive = true;
  let started = false;
  let speaking = false;
  let recording = false;
  let audioContext = null;
  let sourceNode = null;
  let captureNode = null;
  let silenceGain = null;
  let mediaStream = null;
  let frames = [];
  let voiceDetected = false;
  let voiceMillis = 0;
  let lastVoiceAt = 0;
  let capturedSamples = 0;
  let lastValue = '';
  let startingMic = false;
  let currentUtterance = null;
  let maxTimer = null;
  let didSend = false;

  const say = (s) => { if (alive) { status.textContent = s; } };
  const show = () => {
    if (!alive) return;
    startBtn.hidden = started;
    replayBtn.hidden = !started;
    sendBtn.hidden = !recording;
    meter.hidden = !recording;
    finishBtn.disabled = speaking || didSend;
  };
  const stopMicrophone = () => {
    try { captureNode && (captureNode.onaudioprocess = null); } catch (_) {}
    try { captureNode && captureNode.disconnect(); } catch (_) {}
    try { sourceNode && sourceNode.disconnect(); } catch (_) {}
    try { silenceGain && silenceGain.disconnect(); } catch (_) {}
    try { mediaStream && mediaStream.getTracks().forEach(t => t.stop()); } catch (_) {}
    try { audioContext && audioContext.close(); } catch (_) {}
    captureNode = sourceNode = silenceGain = mediaStream = audioContext = null;
    if (maxTimer) clearTimeout(maxTimer);
    maxTimer = null;
  };
  function resample(src, sourceRate, targetRate) {
    if (sourceRate === targetRate) return src;
    const ratio = sourceRate / targetRate;
    const count = Math.max(1, Math.round(src.length / ratio));
    const out = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const x = i * ratio, lo = Math.floor(x), hi = Math.min(src.length - 1, lo + 1);
      const frac = x - lo;
      out[i] = src[lo] * (1-frac) + src[hi] * frac;
    }
    return out;
  }
  function toWavB64(chunks, sourceRate) {
    const length = chunks.reduce((n,c)=>n+c.length,0);
    const signal = new Float32Array(length);
    let offset = 0;
    for (const c of chunks) { signal.set(c,offset); offset += c.length; }
    const sampled = resample(signal, sourceRate, 16000);
    const b = new ArrayBuffer(44 + sampled.length * 2);
    const dv = new DataView(b);
    const write = (at,text) => { for (let i=0;i<text.length;i++) dv.setUint8(at+i,text.charCodeAt(i)); };
    write(0,'RIFF'); dv.setUint32(4,36+sampled.length*2,true);
    write(8,'WAVE'); write(12,'fmt '); dv.setUint32(16,16,true);
    dv.setUint16(20,1,true); dv.setUint16(22,1,true);
    dv.setUint32(24,16000,true); dv.setUint32(28,32000,true);
    dv.setUint16(32,2,true); dv.setUint16(34,16,true);
    write(36,'data'); dv.setUint32(40,sampled.length*2,true);
    let binary = '';
    for (let j=0;j<sampled.length;j++) {
      const value = Math.max(-1, Math.min(1,sampled[j]));
      dv.setInt16(44+j*2, value < 0 ? value*0x8000 : value*0x7fff, true);
    }
    // Éviter un apply() massif : conversion en morceaux.
    const bytes = new Uint8Array(b);
    for (let i=0;i<bytes.length;i+=8192) {
      binary += String.fromCharCode(...bytes.subarray(i,i+8192));
    }
    return btoa(binary);
  }
  function finishCapture(force = false) {
    if (!recording || didSend) return;
    recording = false;
    const snapshot = frames;
    const sampleRate = audioContext?.sampleRate || 16000;
    stopMicrophone(); show();
    if (!voiceDetected && !force) {
      say('Je n’ai pas entendu de réponse. Cliquez sur « Répéter la question » pour réessayer.');
      return;
    }
    if (capturedSamples / sampleRate < 0.6) {
      say('Enregistrement trop court. Réessayez en cliquant sur « Répéter la question ».');
      return;
    }
    didSend = true;
    try {
      say('Réponse reçue. Transcription et préparation de la relance…');
      setTriggerValue('audio', {turn_id: data.turnId, wav_base64: toWavB64(snapshot,sampleRate)});
    } catch (e) {
      didSend = false;
      say('Impossible de transférer cet enregistrement. Utilisez la saisie écrite ci-dessous.');
    }
  }
  async function startCapture() {
    if (!alive || speaking || recording || startingMic || didSend) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      say('Enregistrement automatique indisponible sur ce navigateur. Utilisez la saisie écrite.');
      return;
    }
    startingMic = true;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      if (!alive || didSend) { stopMicrophone(); return; }
      audioContext = new AudioContext();
      await audioContext.resume();
      sourceNode = audioContext.createMediaStreamSource(mediaStream);
      captureNode = audioContext.createScriptProcessor(2048,1,1);
      silenceGain = audioContext.createGain(); silenceGain.gain.value = 0;
      frames = []; voiceDetected = false; voiceMillis = 0;
      capturedSamples = 0; lastVoiceAt = performance.now();
      captureNode.onaudioprocess = (event) => {
        if (!recording || !alive) return;
        const input = event.inputBuffer.getChannelData(0);
        const samples = new Float32Array(input);
        frames.push(samples); capturedSamples += samples.length;
        let power = 0;
        for (let i=0;i<samples.length;i++) power += samples[i]*samples[i];
        const rms = Math.sqrt(power/samples.length);
        const t = performance.now();
        if (rms > 0.014) {
          voiceMillis += 1000*samples.length/audioContext.sampleRate;
          if (voiceMillis >= 220) voiceDetected = true;
          lastVoiceAt = t;
          meter.textContent = 'Voix détectée · je vous écoute…';
        } else {
          voiceMillis = 0;
          if (voiceDetected) meter.textContent = 'Silence · envoi après 5 secondes…';
        }
        if (voiceDetected && t-lastVoiceAt >= 5000) finishCapture(false);
      };
      sourceNode.connect(captureNode);
      captureNode.connect(silenceGain);
      silenceGain.connect(audioContext.destination);
      recording = true;
      say('À vous de parler. Cinq secondes de silence termineront votre réponse.');
      meter.textContent = 'À l’écoute…';
      maxTimer = setTimeout(()=>finishCapture(true),90000);
      show();
    } catch (e) {
      stopMicrophone();
      say('Microphone non autorisé ou indisponible. Autorisez-le dans le navigateur, puis cliquez sur « Répéter la question ».');
    } finally { startingMic = false; }
  }
  function playQuestion() {
    if (!alive || didSend) return;
    stopMicrophone(); recording = false;
    if (!('speechSynthesis' in window)) {
      started = true; show(); say('Lecture vocale indisponible. Cliquez sur « Activer le micro » ou utilisez le clavier.');
      startCapture(); return;
    }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(data.question);
    currentUtterance = u;
    u.lang = 'fr-FR'; u.rate = 0.98;
    const voices = window.speechSynthesis.getVoices();
    u.voice = voices.find(v=>v.lang?.toLowerCase()==='fr-fr') || voices.find(v=>v.lang?.toLowerCase().startsWith('fr')) || null;
    speaking = true; started = true; show(); say('L’assistant vous parle…');
    u.onend = () => { if (!alive || currentUtterance !== u) return; speaking = false; show(); startCapture(); };
    u.onerror = () => { if (!alive || currentUtterance !== u) return; speaking = false; show(); say('Lecture automatique bloquée. Cliquez sur « Répéter la question ».'); };
    window.speechSynthesis.speak(u);
  }
  startBtn.onclick = () => { started = true; playQuestion(); };
  replayBtn.onclick = () => { playQuestion(); };
  sendBtn.onclick = () => finishCapture(true);
  finishBtn.onclick = () => {
    if (didSend) return;
    didSend = true; recording = false; stopMicrophone();
    try { window.speechSynthesis.cancel(); } catch (_) {}
    setTriggerValue('finish_request', {turn_id:data.turnId});
  };
  const keydown = (e) => {
    const active = document.activeElement;
    if (e.key === 'Enter' && recording && active?.tagName !== 'TEXTAREA' && active?.tagName !== 'INPUT') {
      e.preventDefault(); finishCapture(true);
    }
  };
  document.addEventListener('keydown',keydown);
  show();
  const autoKey = 'rid_' + data.turnId;
  // Après la première interaction volontaire, les tours suivants peuvent démarrer seuls.
  if (data.autoStart && window.__ridLastVoiceAuto !== autoKey) {
    window.__ridLastVoiceAuto = autoKey;
    setTimeout(()=>{if(alive) playQuestion();},250);
  }
  return () => {
    alive = false;
    document.removeEventListener('keydown',keydown);
    try { window.speechSynthesis.cancel(); } catch (_) {}
    stopMicrophone();
  };
}
