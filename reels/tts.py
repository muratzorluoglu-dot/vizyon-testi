"""Google Cloud Text-to-Speech (Türkçe) — segment başına WAV üretir.

Kimlik bilgisi yalnızca ortam değişkenlerinden okunur, hiçbir dosyaya yazılmaz:
  GOOGLE_TTS_API_KEY              -> X-Goog-Api-Key başlığı ile REST çağrısı
  GOOGLE_APPLICATION_CREDENTIALS  -> servis hesabı (google-auth kurulu olmalı)
İkisi de yoksa None döner ve akış seslendirmesiz (taslak zamanlı) devam eder.
"""
import base64, json, os, urllib.request, wave

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"


def _auth_headers():
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if key:
        return {"X-Goog-Api-Key": key}
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            import google.auth
            import google.auth.transport.requests
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(google.auth.transport.requests.Request())
            return {"Authorization": f"Bearer {creds.token}"}
        except Exception as e:  # noqa: BLE001
            print(f"[tts] servis hesabı kullanılamadı: {e}")
    return None


def available():
    return _auth_headers() is not None


def _synth(text, voice, rate, headers):
    body = {
        "input": {"text": text},
        "voice": {"languageCode": "tr-TR", "name": voice},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000},
    }
    if rate and rate != 1.0 and "Chirp" not in voice:
        body["audioConfig"]["speakingRate"] = rate
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return base64.b64decode(json.load(r)["audioContent"])


def synthesize(segments, voice_cfg, out_dir):
    """Her segment için out_dir/voice/<id>.wav yazar; {id: süre_sn} döner ya da None."""
    headers = _auth_headers()
    if not headers:
        return None
    os.makedirs(os.path.join(out_dir, "voice"), exist_ok=True)
    durations = {}
    voices = [voice_cfg.get("name", "tr-TR-Wavenet-E")]
    if voice_cfg.get("fallback"):
        voices.append(voice_cfg["fallback"])
    for seg in segments:
        text = seg.get("tts", seg["text"])
        audio, err = None, None
        for v in voices:
            try:
                audio = _synth(text, v, voice_cfg.get("rate", 1.0), headers)
                break
            except Exception as e:  # noqa: BLE001
                err = e
        if audio is None:
            print(f"[tts] {seg['id']} sentezlenemedi: {err}")
            return None
        path = os.path.join(out_dir, "voice", f"{seg['id']}.wav")
        with open(path, "wb") as f:
            f.write(audio)  # LINEAR16 yanıtı WAV başlığıyla gelir
        with wave.open(path) as w:
            durations[seg["id"]] = w.getnframes() / w.getframerate()
    return durations
