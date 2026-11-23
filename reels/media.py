"""Wikimedia Commons'tan lisanslı görsel indirme + atıf kaydı.

episode.json içindeki segmentte:
  "media": {"commons": "File:Örnek.jpg", "focus": [0.5, 0.5], "zoom": [1.0, 1.12], "pan": [0, -0.04]}
İndirilen dosya <bölüm>/media/<id>.jpg olarak (uzun kenar en çok 2400 px) kaydedilir,
yazar/lisans bilgisi <bölüm>/media/credits.json dosyasına yazılır.
Yalnızca CC0, kamu malı, CC BY ve CC BY-SA kabul edilir; NC/ND ve lisanssız dosyalar reddedilir.
"""
import io, json, os, re, time, urllib.error, urllib.parse, urllib.request

UA = {"User-Agent": "reels-video-pipeline/1.0 (educational short video; python-urllib)"}
API = "https://commons.wikimedia.org/w/api.php"
ALLOWED = re.compile(r"^(CC0|Public domain|PD.*|CC BY(-SA)? [0-9.]+.*)$", re.I)


def _get(url, tries=8):
    for a in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90).read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or a == tries - 1:
                raise
            time.sleep(60 + 30 * a)


def _strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def info(title):
    q = urllib.parse.urlencode({"action": "query", "titles": title, "prop": "imageinfo", "format": "json",
                                "formatversion": 2, "iiprop": "url|size|extmetadata"})
    page = json.loads(_get(f"{API}?{q}"))["query"]["pages"][0]
    ii = page["imageinfo"][0]
    m = ii["extmetadata"]
    g = lambda k: _strip(m.get(k, {}).get("value", ""))
    orig = ii["url"].split("?")[0]
    # Wikimedia politikası: orijinal yerine standart boyutlu küçük resim (1920/1280 px) iste
    width = next((w_ for w_ in (1920, 1280) if ii["width"] > w_), None)
    if width:
        name = orig.rsplit("/", 1)[1]
        dl = orig.replace("/wikipedia/commons/", "/wikipedia/commons/thumb/") + f"/{width}px-{name}"
        if name.lower().endswith((".tif", ".tiff")):
            dl += ".jpg"
    else:
        dl = orig
    return {"title": page["title"], "url": orig, "download": dl, "page": ii["descriptionurl"],
            "license": g("LicenseShortName"), "license_url": g("LicenseUrl"),
            "artist": g("Artist") or g("Credit"), "description": g("ImageDescription")[:300]}


def short_credit(c):
    artist = re.sub(r"\s*\(.*?\)", "", c["artist"]).strip()[:40] or "bilinmiyor"
    return f"Foto: {artist} · {c['license']} · Wikimedia Commons"


def fetch(ep, ep_dir, log=print):
    """Eksik görselleri indirir; {seg_id: credit} döner. Ağ yoksa mevcut dosyalarla devam eder."""
    mdir = os.path.join(ep_dir, "media")
    os.makedirs(mdir, exist_ok=True)
    cpath = os.path.join(mdir, "credits.json")
    credits = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
    for seg in ep["segments"]:
        m = seg.get("media")
        if not m or "commons" not in m:
            continue
        out = os.path.join(mdir, f"{seg['id']}.jpg")
        if os.path.exists(out) and credits.get(seg["id"], {}).get("title") == m["commons"]:
            continue
        try:
            c = info(m["commons"])
            if not ALLOWED.match(c["license"]):
                log(f"[media] {seg['id']}: lisans uygun değil ({c['license']}), atlandı")
                continue
            data = _get(c["download"])
            from PIL import Image
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((2400, 2400))  # uzun kenar sınırı
            im.save(out, quality=90)
            c["size"] = im.size
            credits[seg["id"]] = c
            log(f"[media] {seg['id']}: {c['title']} ({c['license']})")
            with open(cpath, "w", encoding="utf-8") as f:
                json.dump(credits, f, ensure_ascii=False, indent=1)
            time.sleep(20)
        except Exception as e:  # noqa: BLE001
            log(f"[media] {seg['id']}: indirilemedi ({e})")
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(credits, f, ensure_ascii=False, indent=1)
    return credits
