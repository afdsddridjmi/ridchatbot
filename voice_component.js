// L'Oreille de RID v3 : aucun secret ni appel Albert côté navigateur.
// Le démarrage sans clic est tenté ; permissions et autoplay dépendent du navigateur.
export default function(component) {
  const {parentElement: root, data, setTriggerValue} = component;
  const $ = (id) => root.querySelector('#' + id);
  const replayBtn = $('rid-replay');
  const sendBtn = $('rid-send');
  const finishBtn = $('rid-finish');
  const status = $('rid-status');
  const meter = $('rid-meter');
  const transcript = $('rid-transcript');
  const count = $('rid-message-count');
  let alive = true;
  let speaking = false;
  let recording = false;
  let startingMic = false;
  let didSend = false;
  let audioContext = null, sourceNode = null, captureNode = null, silenceGain = null, mediaStream = null;
  let frames = [], capturedSamples = 0, voiceDetected = false, candidateMillis = 0;
  let lastVoiceAt = 0, recentRms = [], maxTimer = null, silenceTimer = null;
  let currentUtterance = null, speechWatchdog = null;

  const say = (message) => {if (alive) status.textContent = message;};
  const show = () => {
    if (!alive) return;
    sendBtn.disabled = !recording || didSend;
    finishBtn.disabled = didSend;
    finishBtn.textContent = data.confirmation ? 'Confirmer la conclusion' : 'Je souhaite conclure';
    meter.hidden = !recording;
  };

  // Historique permanent visible, défilement interne automatique vers les dernières paroles.
  for (const m of Array.isArray(data.history) ? data.history : []) {
    const bubble = document.createElement('div');
    bubble.className = 'rid-bubble ' + (m.role === 'user' ? 'rri' : 'bot');
    const title = document.createElement('strong');
    title.textContent = m.role === 'user' ? 'RRI' : "L'Oreille de RID";
    const body = document.createElement('span');
    body.textContent = String(m.content ?? '');
    bubble.appendChild(title); bubble.appendChild(body);
    transcript.appendChild(bubble);
  }
  count.textContent = `${(data.history || []).length} messages`;
  setTimeout(() => {if(alive) transcript.scrollTop = transcript.scrollHeight;}, 50);

  const stopMicrophone = () => {
    try {if (captureNode) captureNode.onaudioprocess = null;} catch (_) {}
    try {captureNode?.disconnect();} catch (_) {}
    try {sourceNode?.disconnect();} catch (_) {}
    try {silenceGain?.disconnect();} catch (_) {}
    try {mediaStream?.getTracks().forEach(t => t.stop());} catch (_) {}
    try {audioContext?.close();} catch (_) {}
    audioContext = sourceNode = captureNode = silenceGain = mediaStream = null;
    if (maxTimer) clearTimeout(maxTimer);
    if (silenceTimer) clearInterval(silenceTimer);
    maxTimer = silenceTimer = null;
  };
  const stopSpeaking = () => {
    if (speechWatchdog) clearTimeout(speechWatchdog);
    speechWatchdog = null;
    try {window.speechSynthesis?.cancel();} catch (_) {}
    currentUtterance = null;
    speaking = false;
  };
  function resample(src, sourceRate, targetRate) {
    if (sourceRate === targetRate) return src;
    const ratio = sourceRate / targetRate;
    const count = Math.max(1, Math.round(src.length / ratio));
    const out = new Float32Array(count);
    for (let i=0;i<count;i++) {
      const x=i*ratio, lo=Math.floor(x), hi=Math.min(src.length-1,lo+1);
      out[i]=src[lo]*(1-(x-lo))+src[hi]*(x-lo);
    }
    return out;
  }
  function toWavB64(chunks, sourceRate) {
    const length=chunks.reduce((n,c)=>n+c.length,0);
    const signal=new Float32Array(length);
    let offset=0;
    for (const chunk of chunks) {signal.set(chunk,offset);offset+=chunk.length;}
    const sampled=resample(signal,sourceRate,16000);
    const b=new ArrayBuffer(44+sampled.length*2), dv=new DataView(b);
    const write=(at,t)=>{for(let j=0;j<t.length;j++)dv.setUint8(at+j,t.charCodeAt(j));};
    write(0,'RIFF'); dv.setUint32(4,36+sampled.length*2,true);
    write(8,'WAVE'); write(12,'fmt '); dv.setUint32(16,16,true);
    dv.setUint16(20,1,true);dv.setUint16(22,1,true);
    dv.setUint32(24,16000,true);dv.setUint32(28,32000,true);
    dv.setUint16(32,2,true);dv.setUint16(34,16,true);
    write(36,'data');dv.setUint32(40,sampled.length*2,true);
    for(let j=0;j<sampled.length;j++) {
      const v=Math.max(-1,Math.min(1,sampled[j]));
      dv.setInt16(44+j*2,v<0?v*32768:v*32767,true);
    }
    const bytes=new Uint8Array(b);
    let binary='';
    for(let j=0;j<bytes.length;j+=8192)binary+=String.fromCharCode(...bytes.subarray(j,j+8192));
    return btoa(binary);
  }
  function finishCapture(force=false) {
    if (!recording || didSend) return;
    const snapshot=frames;
    const rate=audioContext?.sampleRate || 16000;
    const hadVoice=voiceDetected;
    recording=false;
    stopMicrophone();show();
    if (!hadVoice && !force) {
      say('Aucune réponse détectée. Cliquez sur « Répéter la question » pour réessayer.');
      return;
    }
    if (capturedSamples/rate < .6) {
      say('Enregistrement trop court. Cliquez sur « Répéter la question ».');
      return;
    }
    didSend=true;
    try {
      say("Réponse reçue. L'Oreille de RID prépare sa relance…");
      setTriggerValue('audio',{turn_id:data.turnId,wav_base64:toWavB64(snapshot,rate)});
    } catch(_) {
      didSend=false;
      say("L'enregistrement n'a pas pu être transmis. Répondez par écrit ci-dessous.");
    }
    show();
  }

  async function startCapture() {
    if (!alive || recording || speaking || startingMic || didSend) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
      say('Le micro automatique est indisponible sur ce navigateur. Répondez par écrit.');
      return;
    }
    startingMic=true;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}, video:false});
      if (!alive || didSend) {stopMicrophone();return;}
      audioContext=new window.AudioContext();
      await audioContext.resume();
      sourceNode=audioContext.createMediaStreamSource(mediaStream);
      captureNode=audioContext.createScriptProcessor(2048,1,1);
      silenceGain=audioContext.createGain(); silenceGain.gain.value=0;
      frames=[];capturedSamples=0;voiceDetected=false;candidateMillis=0;
      recentRms=[];lastVoiceAt=performance.now();
      captureNode.onaudioprocess=(ev)=>{
        if (!recording || !alive) return;
        const samples=new Float32Array(ev.inputBuffer.getChannelData(0));
        frames.push(samples);capturedSamples+=samples.length;
        let energy=0;
        for (let i=0;i<samples.length;i++)energy+=samples[i]*samples[i];
        const rms=Math.sqrt(energy/samples.length), t=performance.now();
        // Plancher de bruit adaptatif. Un bruit permanent peut dépasser 0.014 :
        // contrairement à la v2, il ne doit plus maintenir « voix détectée » sans fin.
        recentRms.push(rms);
        if(recentRms.length>18)recentRms.shift();
        const sorted=recentRms.slice().sort((a,b)=>a-b);
        const base=recentRms.length >= 6 ? sorted[Math.floor((sorted.length-1)*.25)] : .004;
        const energyThreshold=Math.max(.009,base*1.85,base+.006);
        const candidate=rms>energyThreshold;
        if(candidate){
          candidateMillis=Math.min(1000,candidateMillis + 1000*samples.length/audioContext.sampleRate);
          if(candidateMillis>=180){voiceDetected=true;lastVoiceAt=t;}
        }else{
          candidateMillis=Math.max(0,candidateMillis-1000*samples.length/audioContext.sampleRate);
        }
        if(voiceDetected) {
          const remaining=Math.max(0,Math.ceil((5000-(t-lastVoiceAt))/1000));
          meter.textContent=candidate ? 'Voix détectée · je vous écoute…' : `Silence · envoi dans ${remaining} s…`;
        }else{
          meter.textContent='À l’écoute · parlez librement…';
        }
      };
      sourceNode.connect(captureNode);
      captureNode.connect(silenceGain);
      silenceGain.connect(audioContext.destination);
      recording=true;
      say('À toi de parler. Cinq secondes de silence termineront ta réponse.');
      meter.textContent='À l’écoute…';
      for(const tr of mediaStream.getAudioTracks()) {
        tr.addEventListener('mute',()=>{if(recording && voiceDetected)finishCapture(false);});
        tr.addEventListener('ended',()=>{if(recording && voiceDetected)finishCapture(false);});
      }
      // Vérification sur une vraie horloge, même lorsque le périphérique cesse de fournir des blocs audio.
      silenceTimer=setInterval(()=>{
        if(!alive || !recording)return;
        if(voiceDetected && performance.now()-lastVoiceAt>=5000)finishCapture(false);
      },200);
      maxTimer=setTimeout(()=>{
        if(voiceDetected)finishCapture(true);
        else{recording=false;stopMicrophone();show();say('Je n’ai pas détecté de réponse. Cliquez sur « Répéter la question ».');}
      },90000);
      show();
    }catch(e){
      stopMicrophone();recording=false;
      say('Le navigateur demande une autorisation du micro ou refuse son accès. Cliquez sur « Répéter la question » ou utilisez le clavier.');
      show();
    }finally{startingMic=false;}
  }

  function playQuestion(){
    if(!alive || didSend)return;
    recording=false;stopMicrophone();stopSpeaking();
    if(!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      say('Lecture vocale non prise en charge. Le micro va être proposé.');
      startCapture();return;
    }
    const u=new SpeechSynthesisUtterance(data.question);
    currentUtterance=u;u.lang='fr-FR';u.rate=.98;
    const voices=window.speechSynthesis.getVoices();
    u.voice=voices.find(v=>v.lang?.toLowerCase()==='fr-fr') || voices.find(v=>v.lang?.toLowerCase().startsWith('fr')) || null;
    speaking=true;show();say("L'Oreille de RID vous parle…");
    let started=false;
    u.onstart=()=>{if(!alive || currentUtterance!==u)return;started=true;if(speechWatchdog)clearTimeout(speechWatchdog);};
    u.onend=()=>{if(!alive || currentUtterance!==u)return;started=false;currentUtterance=null;speaking=false;show();startCapture();};
    u.onerror=()=>{if(!alive || currentUtterance!==u)return;currentUtterance=null;speaking=false;show();say('La lecture automatique est bloquée. Cliquez sur « Répéter la question » pour l’activer.');};
    try {window.speechSynthesis.speak(u);} catch(_) {
      currentUtterance=null;speaking=false;show();
      say('Lecture vocale bloquée. Cliquez sur « Répéter la question » pour l’activer.');
      return;
    }
    speechWatchdog=setTimeout(()=>{
      if(alive && speaking && currentUtterance===u && !started){
        stopSpeaking();show();
        say('La lecture automatique semble bloquée par le navigateur. Cliquez sur « Répéter la question » pour l’activer.');
      }
    },5500);
  }

  replayBtn.onclick=()=>playQuestion();
  sendBtn.onclick=()=>finishCapture(true);
  finishBtn.onclick=()=>{
    if(didSend)return;
    didSend=true;recording=false;stopMicrophone();stopSpeaking();show();
    setTriggerValue('finish_request',{turn_id:data.turnId});
  };
  const keydown=(ev)=>{
    const tag=document.activeElement?.tagName;
    if(ev.key==='Enter' && recording && tag!=='TEXTAREA' && tag!=='INPUT' && !ev.isComposing){
      ev.preventDefault();finishCapture(true);
    }
  };
  document.addEventListener('keydown',keydown);
  show();

  // Aucun bouton d'accueil : tentative immédiate, une seule fois par question.
  // L'utilisateur peut néanmoins devoir cliquer si le navigateur bloque l'autoplay.
  if(window.__ridLastVoiceTurn !== data.turnId) {
    window.__ridLastVoiceTurn=data.turnId;
    setTimeout(()=>{if(alive)playQuestion();},450);
    setTimeout(()=>{
      if(!alive)return;
      transcript.scrollTop=transcript.scrollHeight;
      const dock = root.closest('.st-key-rid_conversation_dock');
      if (dock) {dock.scrollTop=dock.scrollHeight; dock.scrollIntoView({block:'end',behavior:'auto'});}
    },700);
  }
  return ()=>{
    alive=false;
    document.removeEventListener('keydown',keydown);
    stopSpeaking();stopMicrophone();
  };
}
