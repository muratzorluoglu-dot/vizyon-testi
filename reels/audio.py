"""Kodla üretilen müzik yatağı, ortam sesi ve geçiş efektleri + seslendirme miksi.
Dış ses dosyası kullanılmaz; lisans sorunu yoktur."""
import os, wave
import numpy as np
from scipy.signal import lfilter

SR = 48000


def _lp(x, a):
    """Tek kutuplu alçak geçiren filtre (a: 0..1, küçük = daha koyu)."""
    return lfilter([a], [1, a - 1], x)


def _noise(n, rng):
    return rng.standard_normal(n).astype(np.float64)


def music_bed(dur, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    # Re minör çevresinde yavaş akorlar: Dm9 - Bbmaj7 - Fmaj7 - C6/9
    chords = [[146.83, 220.0, 261.63, 329.63], [116.54, 174.61, 220.0, 293.66],
              [87.31, 174.61, 220.0, 261.63], [130.81, 196.0, 246.94, 293.66]]
    seg = 8.0
    out = np.zeros(n)
    for i, ch in enumerate(chords * int(np.ceil(dur / (seg * 4)) + 1)):
        s = int(i * seg * SR)
        if s >= n:
            break
        e = min(n, int((i + 1) * seg * SR + 2 * SR))
        tt = t[s:e] - t[s]
        env = np.minimum(1, tt / 2.5) * np.exp(-np.maximum(0, tt - seg) / 0.9)
        for k, f in enumerate(ch):
            det = 1 + 0.0025 * np.sin(2 * np.pi * 0.11 * tt + k)
            tone = np.sin(2 * np.pi * f * det * tt) + 0.25 * np.sin(2 * np.pi * 2 * f * tt + k)
            out[s:e] += tone * env * (0.10 if k else 0.14)
    # su altı "akıntı" dokusu: filtrelenmiş gürültü, yavaş LFO
    amb = _lp(_noise(n, rng), 0.004) * 6
    amb *= 0.5 + 0.5 * np.sin(2 * np.pi * 0.07 * t)
    out += amb * 0.10
    # yumuşak giriş/çıkış
    fade = np.minimum(1, t / 1.2) * np.minimum(1, (dur - t) / 2.0).clip(0, 1)
    return out * fade


def whoosh(rng, length=0.55):
    """Bant geçiren gürültü; parlaklığı ortada tepe yapan kısa süpürme."""
    n = int(length * SR)
    x = _noise(n, rng)
    tt = np.linspace(0, 1, n)
    dark = _lp(x, 0.02)
    bright = _lp(x, 0.18) - _lp(x, 0.03)
    mix = dark * (1 - tt) * 3 + bright * np.sin(np.pi * tt) * 2
    return mix * np.sin(np.pi * tt) ** 2 * 0.5


def bubbles(rng, length=1.2, count=9):
    n = int(length * SR)
    y = np.zeros(n)
    for _ in range(count):
        s = rng.integers(0, int(n * 0.8))
        d = int(SR * rng.uniform(0.04, 0.09))
        tt = np.arange(d) / SR
        f0 = rng.uniform(500, 1100)
        f = f0 * (1 + 3 * tt / tt[-1])
        ph = 2 * np.pi * np.cumsum(f) / SR
        y[s:s + d] += np.sin(ph) * np.exp(-tt * 40) * 0.35
    return y


def tick(rng, f=1400):
    d = int(SR * 0.08)
    tt = np.arange(d) / SR
    return np.sin(2 * np.pi * f * tt) * np.exp(-tt * 70) * 0.25


def thump(rng, f=70):
    d = int(SR * 0.6)
    tt = np.arange(d) / SR
    return np.sin(2 * np.pi * f * (1 - 0.3 * tt) * tt) * np.exp(-tt * 7) * 0.6


def _place(buf, clip, at):
    s = int(at * SR)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(clip))
    buf[s:e] += clip[: e - s]


def _read_wav(path):
    with wave.open(path) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    if sr != SR:
        x = np.interp(np.arange(int(len(x) * SR / sr)) * sr / SR, np.arange(len(x)), x)
    return x


def build_mix(timeline, out_path, voice_dir=None, seed=7):
    rng = np.random.default_rng(seed)
    dur = timeline["duration"]
    n = int(dur * SR)
    music = music_bed(dur, rng)
    sfx = np.zeros(n)
    voice = np.zeros(n)
    for i, sc in enumerate(timeline["scenes"]):
        st = sc["start"]
        kind = sc["scene"]
        if i == 0:
            _place(sfx, bubbles(rng, 1.6, 12) * 0.8, 0.15)
            _place(sfx, thump(rng, 55) * 0.6, 0.0)
        else:
            _place(sfx, whoosh(rng, 0.5) * 0.35, max(0, st - 0.25))
        if kind == "title":
            for k in range(10):
                _place(sfx, tick(rng, 1200 + 60 * k), st + 0.9 + k * 0.22)
        if kind in ("angelita", "manta", "steps", "grid"):
            _place(sfx, thump(rng, 60 if kind != "steps" else 110) * 0.5, st + 0.2)
        if kind in ("silfra", "reef"):
            _place(sfx, bubbles(rng, 1.0, 6) * 0.5, st + 0.3)
        if voice_dir and os.path.exists(os.path.join(voice_dir, f"{sc['id']}.wav")):
            _place(voice, _read_wav(os.path.join(voice_dir, f"{sc['id']}.wav")), sc["voice_start"])
    has_voice = np.any(voice)
    if has_voice:
        env = np.abs(voice)
        env = lfilter([0.002], [1, -0.998], env)
        duck = 1 - np.clip(env * 25, 0, 0.7)
        music *= duck
        mix = voice * 0.95 + music * 0.55 + sfx * 0.5
    else:
        mix = music * 0.9 + sfx * 0.6
    peak = np.max(np.abs(mix)) or 1
    mix = mix / peak * 0.89
    # hafif stereo genişlik: müzik/efekt için küçük gecikme
    d = int(SR * 0.012)
    left = mix
    right = np.concatenate([mix[:d], mix[:-d]]) * 0.5 + mix * 0.5 if not has_voice else mix
    st = np.stack([left, right], 1)
    pcm = (np.clip(st, -1, 1) * 32767).astype(np.int16)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return has_voice
