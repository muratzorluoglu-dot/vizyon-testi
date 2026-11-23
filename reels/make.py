#!/usr/bin/env python3
"""Kısa dikey video üretim akışı — tek giriş noktası.

Kullanım:
  python3 reels/make.py new "Türkiye'nin En Yüksek 10 Dağı"     # bölüm iskeleti oluşturur
  python3 reels/make.py build reels/episodes/<slug>              # tam üretim
  python3 reels/make.py build reels/episodes/<slug> --preview    # yarım çözünürlük hızlı deneme
  python3 reels/make.py still reels/episodes/<slug> 12.5         # tek kare PNG
  python3 reels/make.py docs reels/episodes/<slug>               # yalnızca metin paketini yenile
  python3 reels/make.py media reels/episodes/<slug>              # yalnızca Commons görsellerini indir

Akış: episode.json -> (Google TTS) -> süre ölçümü -> zamanlama -> SRT ->
      sahne verisi -> ses miksi -> render (MP4) -> ffprobe kontrolü -> script.md
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VOWELS = set("aeıioöuüâîûAEIİOÖUÜÂÎÛ")
SYL_PER_SEC = 5.8      # Türkçe anlatım için tahmini hız (ses yoksa)
LEAD, TAIL, END_TAIL = 0.1, 0.22, 1.3


# ---------------------------------------------------------------- zamanlama
def syllables(s):
    return max(1, sum(c in VOWELS for c in s))


def estimate_speech(seg):
    s = seg.get("tts", seg["text"])
    pauses = len(re.findall(r"[.,;:?!](?=\s)", s))
    return syllables(s) / SYL_PER_SEC + 0.22 * pauses


def build_timeline(ep, measured=None):
    scenes, t, idx = [], 0.0, 0
    total = sum(1 for s in ep["segments"] if "place" in s)
    prev_ll = None
    first_item = True
    segs = ep["segments"]
    for i, seg in enumerate(segs):
        speech = measured[seg["id"]] if measured else estimate_speech(seg)
        lead = 0.2 if i == 0 else LEAD
        tail = END_TAIL if i == len(segs) - 1 else TAIL
        sc = {"id": seg["id"], "scene": seg.get("scene", "typo"), "start": round(t, 3),
              "voice_start": round(t + lead, 3), "speech": round(speech, 3),
              "dur": round(lead + speech + tail, 3), "seg": seg, "total": total}
        if i == 0:
            sc["fullbleed"] = True
        if "place" in seg:
            idx += 1
            sc["index"] = idx
            sc["prev_lonlat"] = prev_ll
            prev_ll = seg.get("lonlat")
            if first_item and segs[0].get("scene") == seg.get("scene"):
                sc["zoom_from_full"] = True
            first_item = False
        scenes.append(sc)
        t += sc["dur"]
    return {"duration": round(t, 3), "scenes": scenes, "measured": bool(measured)}


# ---------------------------------------------------------------- altyazı
def _balance(words, max_line):
    """Kelimeleri en dengeli 1-2 satıra böl."""
    one = " ".join(words)
    if len(one) <= max_line or len(words) == 1:
        return [one]
    def cost(k):
        a, b = len(" ".join(words[:k])), len(" ".join(words[k:]))
        c = max(a, b)
        if max(a, b) > max_line + 9:
            c += 100
        if words[k - 1].endswith((".", ",", ";", ":", "?", "!")):
            c -= 12  # noktalamadan sonra satır kırmayı tercih et
        return c
    best = min(range(1, len(words)), key=cost)
    return [" ".join(words[:best]), " ".join(words[best:])]


def chunk_text(text, max_line=27):
    """Konuşmayı anlamlı bloklara böl: önce noktalamada kes, sonra en çok 2 dengeli satır."""
    phrases = [p.strip() for p in re.split(r"(?<=[.,;:?!])\s+", text) if p.strip()]
    # çok kısa ifadeleri sonrakiyle birleştir ("Silfra, İzlanda." gibi)
    groups = []
    for ph in phrases:
        prev = groups[-1] if groups else ""
        fits_line = len(prev) + len(ph) + 1 <= max_line and not prev.endswith((".", "?", "!"))
        short_prev = 0 < len(prev) <= 16 and len(prev) + len(ph) + 1 <= 2 * max_line
        if groups and (fits_line or short_prev):
            groups[-1] += " " + ph
        else:
            groups.append(ph)
    blocks = []
    for g in groups:
        words = g.split()
        while words:
            # 2 satıra sığan en uzun önek
            k = len(words)
            while k > 1 and len(" ".join(words[:k])) > 2 * max_line:
                k -= 1
            blocks.append(_balance(words[:k], max_line))
            words = words[k:]
    return blocks


def build_cues(timeline):
    cues = []
    for sc in timeline["scenes"]:
        blocks = chunk_text(sc["seg"]["text"])
        weights = [syllables(" ".join(b)) for b in blocks]
        tot = sum(weights)
        t = sc["voice_start"]
        for b, wgt in zip(blocks, weights):
            d = sc["speech"] * wgt / tot
            cues.append({"start": round(t, 3), "end": round(t + d, 3), "lines": b})
            t += d
    # boşlukları kapat (bir sonraki blok gelene kadar kısa süre ekranda kalsın)
    for a, b in zip(cues, cues[1:]):
        if 0 < b["start"] - a["end"] < 0.5:
            a["end"] = b["start"]
    return cues


def ts(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n" + "\n".join(c["lines"]) + "\n\n")


# ---------------------------------------------------------------- dokümanlar
def mmss(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def write_docs(ep, tl, cues, out_dir, srt_name, status, credits=None):
    b = ep["brief"]
    L = []
    L.append(f"# {ep['title']}\n")
    L.append("## 1. Video kartı\n")
    L.append(f"- **Başlık:** {ep['title']}")
    L.append(f"- **Brif:** {b['scope']} · {b['count']} {b['item_type']} · Ölçüt: {b['criterion']}")
    L.append(f"- **Ölçütün dayanağı:** {b['criterion_basis']}")
    L.append(f"- **Açı:** {ep['angle']}")
    L.append(f"- **İzleyicinin kazancı:** {ep['viewer_gain']}")
    L.append(f"- **Süre:** {tl['duration']:.1f} sn ({'ölçülen ses' if tl['measured'] else 'tahmini, ses yok'}) · 1080×1920 · 30 fps")
    L.append(f"- **Ana açılış (sahne/eylem):** “{ep['segments'][0]['text']}”")
    for i, h in enumerate(ep["alt_hooks"], 1):
        L.append(f"- **A/B açılış {i}:** {h}")
    L.append("\n## 2. Konuşma metni\n")
    L.append(" ".join(s["text"] for s in ep["segments"]) + "\n")
    tts_notes = [s for s in ep["segments"] if "tts" in s]
    if tts_notes:
        L.append("**Telaffuz notları (seslendirmeye giden yazım):**\n")
        for s in tts_notes:
            L.append(f"- {s['id']}: “{s['tts']}”")
    words = sum(len(s["text"].split()) for s in ep["segments"])
    L.append(f"\n_{words} kelime._\n")
    L.append("## 3. Sahne planı\n")
    L.append("| Zaman | Konuşma | Görsel / kamera / animasyon | Ekran metni | Ses |")
    L.append("|---|---|---|---|---|")
    for sc in tl["scenes"]:
        s = sc["seg"]
        end = sc["start"] + sc["dur"]
        vis = s.get("visual", "Tipografik hareketli metin.") + (f" _Neden şimdi:_ {s['why']}" if s.get("why") else "")
        L.append(f"| {mmss(sc['start'])}–{mmss(end)} | {s['text']} | {vis} | {s.get('onscreen', '')} | {s.get('sfx', '')} |")
    L.append(f"\n## 4. Altyazı ({'gerçek sese hizalı' if tl['measured'] else 'TASLAK zamanlama — ses oluşunca yeniden üretilir'})\n")
    L.append("```srt\n" + open(os.path.join(out_dir, srt_name), encoding="utf-8").read().strip() + "\n```\n")
    pub = ep.get("publish", {})
    L.append("## 5. Yayın paketi\n")
    L.append("**Kapak metni seçenekleri:**\n")
    for c in pub.get("covers", []):
        L.append(f"- {c}")
    L.append(f"\n**Paylaşım açıklaması:**\n\n{pub.get('caption', '')}\n")
    L.append("**Etiketler:** " + " ".join(pub.get("tags", [])) + "\n")
    if ep.get("sources"):
        L.append("**Kaynaklar (editör için):**\n")
        for label, url in ep["sources"]:
            L.append(f"- {label}: {url}")
    if credits:
        L.append("\n**Fotoğraf atıfları (açıklamaya da eklenmeli; CC BY lisansı atıf ister):**\n")
        for seg in ep["segments"]:
            c = credits.get(seg["id"])
            if c:
                rep = " — _temsilî görüntü, konum birebir bu nokta değil_" if seg.get("media", {}).get("representative") else ""
                L.append(f"- {seg.get('place', seg['id'])}: “{c['title'][5:]}”, {c['artist'][:80]}, {c['license']} ({c['license_url']}), {c['page']}{rep}")
        L.append("\nFotoğraflar kırpıldı, yavaş zoom/kaydırma uygulandı ve renkleri koyulaştırıldı (değişiklik yapılmıştır).")
    L.append("\n**Diğer varlıklar:** Harita, oran/ölçek grafikleri ve fotoğrafı olmayan sahneler kodla çizilmiş motion graphics'tir. "
             "Harita: Natural Earth 1:110m kara verisi (kamu malı), world-atlas paketi (ISC). Müzik ve efektler kodla sentezlendi. "
             "Yazı tipi: Inter (SIL OFL).\n")
    L.append("## 6. Üretim durumu\n")
    for line in status:
        L.append(f"- {line}")
    with open(os.path.join(out_dir, "script.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------- kontrol
def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = [s for s in j["streams"] if s["codec_type"] == "audio"]
    num, den = map(int, v["r_frame_rate"].split("/"))
    return {"width": v["width"], "height": v["height"], "fps": num / den,
            "duration": float(j["format"]["duration"]), "audio_streams": len(a),
            "audio": f"{a[0]['codec_name']} {a[0]['sample_rate']} Hz {a[0]['channels']} kanal" if a else "yok"}


# ---------------------------------------------------------------- komutlar
TR = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosucgiosuaiu")


def slugify(title):
    s = re.sub(r"['’]", "", title).translate(TR).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def cmd_new(title):
    slug = slugify(title)
    d = os.path.join(HERE, "episodes", slug)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "episode.json")
    if os.path.exists(path):
        print(f"Zaten var: {path}")
        return d
    ep = {
        "title": title,
        "brief": {"format": "?", "scope": "?", "item_type": "?", "count": 0, "criterion": "?",
                  "criterion_basis": "Brif tamamlanınca doldurulacak."},
        "angle": "?", "viewer_gain": "?", "alt_hooks": ["?", "?"],
        "palette": {"bg_top": "#2B2540", "bg_bottom": "#120F1C", "ink": "#F4EEF8", "muted": "#B9AFC9",
                    "accent": "#C9A7EB", "aqua": "#A7D7E8", "sand": "#E8DCC8", "deep": "#0D0A14"},
        "voice": {"provider": "google", "name": "tr-TR-Chirp3-HD-Charon", "fallback": "tr-TR-Wavenet-E", "rate": 1.0},
        "segments": [
            {"id": "hook", "scene": "typo", "text": f"{title}.", "onscreen": title},
            {"id": "context", "scene": "typo", "text": "Bağlam ve vaat cümlesi buraya.", "onscreen": "Vaat"},
            {"id": "body", "scene": "typo", "text": "Örnekler ve ilerleme buraya.", "onscreen": "Örnek"},
            {"id": "payoff", "scene": "typo", "text": "Açılışın karşılığı buraya.", "onscreen": "Karşılık"},
            {"id": "cta", "scene": "typo", "text": "Tek bir kapanış çağrısı.", "onscreen": "Kapanış"}],
        "publish": {"covers": [], "caption": "", "tags": []}, "sources": []}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False, indent=2)
    print(f"İskelet oluşturuldu: {path}")
    return d


def attach_media(ep, ep_dir, download=True):
    """Görselleri indir (gerekirse) ve segmentlere yerel yol + kısa atıf ekle."""
    import media
    credits = media.fetch(ep, ep_dir) if download else {}
    cpath = os.path.join(ep_dir, "media", "credits.json")
    if not credits and os.path.exists(cpath):
        credits = json.load(open(cpath, encoding="utf-8"))
    for seg in ep["segments"]:
        m = seg.get("media")
        path = os.path.join(ep_dir, "media", f"{seg['id']}.jpg")
        if m and seg["id"] in credits and os.path.exists(path):
            m["_path"] = path
            seg["_credit"] = media.short_credit(credits[seg["id"]]) + (" · Temsilî görüntü" if m.get("representative") else "")
    return credits


def cmd_build(ep_dir, preview=False, frames=None):
    import audio, render, tts
    ep = json.load(open(os.path.join(ep_dir, "episode.json"), encoding="utf-8"))
    status = []
    print("0/6 Görseller")
    credits = attach_media(ep, ep_dir)
    want = sum(1 for s in ep["segments"] if s.get("media"))
    if want:
        status.append(f"Görseller: {len(credits)}/{want} Wikimedia Commons dosyası → media/ (atıflar media/credits.json)")
    print("1/6 Seslendirme")
    measured = tts.synthesize(ep["segments"], ep.get("voice", {}), ep_dir) if tts.available() else None
    if measured:
        status.append(f"Seslendirme: Google TTS ({ep['voice']['name']}), {len(measured)} segment → voice/*.wav")
    else:
        status.append("Seslendirme: TAMAMLANAMADI — GOOGLE_TTS_API_KEY veya GOOGLE_APPLICATION_CREDENTIALS tanımlı değil. "
                      "Video müzik+efekt ve gömülü altyazıyla, tahmini konuşma zamanlamasıyla üretildi.")
    print("2/6 Zamanlama")
    tl = build_timeline(ep, measured)
    print(f"   süre {tl['duration']:.2f} sn")
    print("3/6 Altyazı")
    cues = build_cues(tl)
    srt_name = "altyazi.srt" if measured else "altyazi.taslak.srt"
    for old in ("altyazi.srt", "altyazi.taslak.srt"):
        if old != srt_name and os.path.exists(os.path.join(ep_dir, old)):
            os.remove(os.path.join(ep_dir, old))
    write_srt(cues, os.path.join(ep_dir, srt_name))
    status.append(f"Altyazı: {srt_name} ({len(cues)} blok, {'gerçek sese hizalı' if measured else 'taslak zamanlama'})")
    scenes_out = [{k: v for k, v in sc.items() if k != "seg"} | {"text": sc["seg"]["text"]} for sc in tl["scenes"]]
    with open(os.path.join(ep_dir, "scenes.json"), "w", encoding="utf-8") as f:
        json.dump({"duration": tl["duration"], "scenes": scenes_out}, f, ensure_ascii=False, indent=2)
    status.append("Sahne verisi: scenes.json")
    print("4/6 Ses miksi")
    mix = os.path.join(ep_dir, "mix.wav")
    audio.build_mix(tl, mix, os.path.join(ep_dir, "voice") if measured else None)
    status.append("Ses: mix.wav (48 kHz stereo; kodla üretilmiş müzik yatağı ve efektler" +
                  (", seslendirme ducking ile)" if measured else ", seslendirme yok)"))
    print("5/6 Render")
    out = os.path.join(ep_dir, "preview.mp4" if preview else "video.mp4")
    render.render(ep, tl, cues, mix, out, scale=0.5 if preview else 1.0, frames_limit=frames)
    print("6/6 Kontrol")
    info = probe(out)
    ok = (info["width"], info["height"]) == ((540, 960) if preview else (1080, 1920))
    status.append(f"Video: {os.path.basename(out)} — {info['width']}×{info['height']} "
                  f"({info['width'] / info['height']:.4f} ≈ 9:16), {info['fps']:.0f} fps, "
                  f"{info['duration']:.2f} sn, ses akışı: {info['audio']}" + ("" if ok else " ⚠ çözünürlük beklenenden farklı"))
    write_docs(ep, tl, cues, ep_dir, srt_name, status, credits)
    status_print = "\n".join(status)
    print(status_print)
    return info


def cmd_docs(ep_dir):
    """Render etmeden script.md / SRT / scenes.json'u yeniler (video.mp4 varsa yeniden ölçer)."""
    ep = json.load(open(os.path.join(ep_dir, "episode.json"), encoding="utf-8"))
    vdir = os.path.join(ep_dir, "voice")
    measured = None
    if os.path.isdir(vdir):
        import wave
        try:
            measured = {}
            for s in ep["segments"]:
                with wave.open(os.path.join(vdir, f"{s['id']}.wav")) as w:
                    measured[s["id"]] = w.getnframes() / w.getframerate()
        except FileNotFoundError:
            measured = None
    tl = build_timeline(ep, measured)
    cues = build_cues(tl)
    credits = attach_media(ep, ep_dir, download=False)
    srt_name = "altyazi.srt" if measured else "altyazi.taslak.srt"
    write_srt(cues, os.path.join(ep_dir, srt_name))
    status = [l[2:] for l in open(os.path.join(ep_dir, "script.md"), encoding="utf-8").read()
              .split("## 6. Üretim durumu")[-1].strip().splitlines() if l.startswith("- ")]
    write_docs(ep, tl, cues, ep_dir, srt_name, status, credits)
    print(os.path.join(ep_dir, "script.md"))


def cmd_still(ep_dir, t):
    import render
    ep = json.load(open(os.path.join(ep_dir, "episode.json"), encoding="utf-8"))
    attach_media(ep, ep_dir, download=False)
    tl = build_timeline(ep)
    cues = build_cues(tl)
    out = os.path.join(ep_dir, f"still_{t:05.1f}.png")
    render.still(ep, tl, cues, t, out)
    print(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("title")
    b = sub.add_parser("build")
    b.add_argument("episode_dir")
    b.add_argument("--preview", action="store_true")
    b.add_argument("--frames", type=int)
    md = sub.add_parser("media")
    md.add_argument("episode_dir")
    dc = sub.add_parser("docs")
    dc.add_argument("episode_dir")
    s = sub.add_parser("still")
    s.add_argument("episode_dir")
    s.add_argument("t", type=float)
    a = ap.parse_args()
    if a.cmd == "new":
        cmd_new(a.title)
    elif a.cmd == "build":
        cmd_build(a.episode_dir, a.preview, a.frames)
    elif a.cmd == "media":
        ep = json.load(open(os.path.join(a.episode_dir, "episode.json"), encoding="utf-8"))
        import media
        c = media.fetch(ep, a.episode_dir)
        want = [s["id"] for s in ep["segments"] if s.get("media")]
        missing = [i for i in want if i not in c or not os.path.exists(os.path.join(a.episode_dir, "media", f"{i}.jpg"))]
        print(f"{len(want) - len(missing)}/{len(want)} görsel hazır" + (f"; eksik: {', '.join(missing)}" if missing else ""))
    elif a.cmd == "docs":
        cmd_docs(a.episode_dir)
    else:
        cmd_still(a.episode_dir, a.t)


if __name__ == "__main__":
    main()
