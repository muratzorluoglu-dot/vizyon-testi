"""Skia ile kare kare dikey video render'ı (1080x1920, 30 fps) -> ffmpeg.

Her sahne tipi bir "painter" fonksiyonudur: painter(ctx, rect, t, p).
Bilinmeyen sahne tipleri tipografik 'typo' çizerine düşer, böylece yeni bir
başlıkla iskelet bölüm de render edilebilir.
"""
import json, math, os, subprocess
import numpy as np
import skia

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = "/usr/share/fonts/opentype/inter"
XFADE = 0.35  # sahne geçişi (sn)

# Güvenli alanlar: üst ~200 px, sağ ~140 px (etkileşim düğmeleri), alt ~330 px (arayüz)
PANEL = (70, 470, 860, 700)  # x, y, w, h  (sağda 150 px boşluk)


def _tf(name):
    return skia.Typeface.MakeFromFile(os.path.join(FONT_DIR, name))


TF = {"bold": _tf("InterDisplay-Bold.otf"), "semi": _tf("InterDisplay-SemiBold.otf"),
      "med": _tf("InterDisplay-Medium.otf"), "reg": _tf("Inter-Regular.otf"),
      "tsemi": _tf("Inter-SemiBold.otf")}


def hexc(h, a=1.0):
    h = h.lstrip("#")
    return skia.Color4f(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def paint(color, a=None, stroke=None, blur=None, cap_round=False):
    c = color if isinstance(color, skia.Color4f) else hexc(color)
    if a is not None:
        c = skia.Color4f(c.fR, c.fG, c.fB, c.fA * a)
    p = skia.Paint(Color4f=c, AntiAlias=True)
    if stroke:
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(stroke)
    if cap_round:
        p.setStrokeCap(skia.Paint.kRound_Cap)
        p.setStrokeJoin(skia.Paint.kRound_Join)
    if blur:
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    return p


def ease(x):
    x = min(1, max(0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = min(1, max(0, x))
    return 1 - (1 - x) ** 3


def lerp(a, b, k):
    return a + (b - a) * k


def text(canvas, s, x, y, size, color, weight="bold", a=1.0, align="left", tracking=0):
    font = skia.Font(TF[weight], size)
    if tracking:
        font.setScaleX(1.0)
    w = font.measureText(s)
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    canvas.drawString(s, x, y, font, paint(color, a))
    return w


def text_width(s, size, weight="bold"):
    return skia.Font(TF[weight], size).measureText(s)


def wrap(s, size, maxw, weight="bold"):
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        cand = (cur + " " + w_).strip()
        if text_width(cand, size, weight) <= maxw or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- arka plan
class Ctx:
    def __init__(self, ep, timeline):
        self.ep = ep
        self.pal = ep["palette"]
        self.tl = timeline
        rng = np.random.default_rng(3)
        g = (rng.random((H + 64, W + 64)) * 255).astype(np.uint8)
        rgba = np.zeros((H + 64, W + 64, 4), np.uint8)
        rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = g
        rgba[..., 3] = 12
        self.grain = skia.Image.fromarray(rgba, colorType=skia.kRGBA_8888_ColorType)
        land = json.load(open(os.path.join(HERE, "assets", "land-110m.json")))
        self.land = land
        self.sites = [s for s in ep["segments"] if "lonlat" in s]
        self.cache = {}
        self.images = {}

    def image(self, path):
        if path not in self.images:
            self.images[path] = skia.Image.MakeFromEncoded(skia.Data.MakeFromFileName(path)) \
                if path and os.path.exists(path) else None
        return self.images[path]

    def c(self, key, a=None):
        return hexc(self.pal[key], 1.0 if a is None else a)


def draw_bg(canvas, ctx, frame):
    sh = skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(0, H)],
                                        [ctx.c("bg_top"), ctx.c("bg_bottom")])
    p = skia.Paint(Shader=sh)
    canvas.drawRect(skia.Rect.MakeWH(W, H), p)


def draw_grain(canvas, ctx, frame):
    rng = np.random.default_rng(frame // 3)  # doku 10 Hz değişir: canlı ama sıkıştırılabilir
    ox, oy = rng.integers(0, 64, 2)
    canvas.drawImage(ctx.grain, -float(ox), -float(oy))
    # vinyet
    sh = skia.GradientShader.MakeRadial(skia.Point(W / 2, H * 0.45), H * 0.75,
                                        [skia.Color4f(0, 0, 0, 0), skia.Color4f(0, 0, 0, 0.45)])
    canvas.drawRect(skia.Rect.MakeWH(W, H), skia.Paint(Shader=sh))


# ---------------------------------------------------------------- ortak öğeler
def water(canvas, r, top, bottom):
    x, y, w, h = r
    sh = skia.GradientShader.MakeLinear([skia.Point(x, y), skia.Point(x, y + h)], [top, bottom])
    canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), skia.Paint(Shader=sh))


def rays(canvas, r, t, a=0.08, n=5):
    x, y, w, h = r
    for i in range(n):
        cx = x + w * (0.15 + 0.7 * i / max(1, n - 1)) + 40 * math.sin(t * 0.4 + i)
        path = skia.Path()
        path.moveTo(cx - 30, y)
        path.lineTo(cx + 30, y)
        path.lineTo(cx + 160 + 30 * math.sin(i), y + h)
        path.lineTo(cx + 40, y + h)
        path.close()
        sh = skia.GradientShader.MakeLinear([skia.Point(0, y), skia.Point(0, y + h)],
                                            [skia.Color4f(1, 1, 1, a), skia.Color4f(1, 1, 1, 0)])
        canvas.drawPath(path, skia.Paint(Shader=sh, AntiAlias=True))


def diver(canvas, x, y, s, color, ang=0.35, kick=0.0):
    canvas.save()
    canvas.translate(x, y)
    canvas.rotate(math.degrees(ang))
    canvas.scale(s, s)
    p = paint(color)
    canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(-60, -14, 105, 28), 14, 14), p)  # gövde
    canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(-40, -30, 62, 18), 9, 9), p)  # tüp
    canvas.drawCircle(58, -2, 15, p)  # baş
    for k, d in ((1, 1), (-1, -1)):
        leg = skia.Path()
        leg.moveTo(-58, -6 * k)
        ang2 = kick * d
        lx, ly = -105, -6 * k + 14 * ang2
        leg.lineTo(lx, ly)
        canvas.drawPath(leg, paint(color, stroke=13, cap_round=True))
        fin = skia.Path()
        fin.moveTo(lx, ly - 8)
        fin.lineTo(lx - 46, ly - 16 + 18 * ang2)
        fin.lineTo(lx - 46, ly + 10 + 18 * ang2)
        fin.lineTo(lx, ly + 8)
        fin.close()
        canvas.drawPath(fin, p)
    arm = skia.Path()
    arm.moveTo(30, 6)
    arm.lineTo(70, 22)
    canvas.drawPath(arm, paint(color, stroke=10, cap_round=True))
    canvas.restore()


def bubbles_up(canvas, x, y, t, n=8, seed=0, a=0.6, spread=18, rise=120):
    rng = np.random.default_rng(seed)
    for i in range(n):
        ph = (t * 0.8 + rng.random()) % 1
        bx = x + spread * math.sin(ph * 9 + i) + rng.uniform(-6, 6)
        by = y - ph * rise * (1 + rng.random())
        canvas.drawCircle(bx, by, 2 + 5 * rng.random(), paint("#FFFFFF", a * (1 - ph), stroke=2))


def fish_shape(canvas, x, y, L, color, a=1.0, ang=0.0):
    canvas.save()
    canvas.translate(x, y)
    canvas.rotate(math.degrees(ang))
    p = paint(color, a)
    canvas.drawOval(skia.Rect.MakeXYWH(-L / 2, -L / 5, L, L * 0.4), p)
    tail = skia.Path()
    tail.moveTo(L * 0.4, 0)
    tail.lineTo(L * 0.75, -L * 0.22)
    tail.lineTo(L * 0.75, L * 0.22)
    tail.close()
    canvas.drawPath(tail, p)
    canvas.restore()


def scale_bar(canvas, x, y0, y1, k, label_top, label_bot, ctx):
    canvas.drawLine(x, y0, x, lerp(y0, y1, k), paint(ctx.c("ink"), 0.8, stroke=3))
    for yy, lab in ((y0, label_top), (lerp(y0, y1, k), label_bot)):
        canvas.drawLine(x - 10, yy, x + 10, yy, paint(ctx.c("ink"), 0.8, stroke=3))
    text(canvas, label_top, x + 20, y0 + 10, 28, ctx.c("ink"), "semi", 0.85)
    if k > 0.05:
        text(canvas, label_bot, x + 20, lerp(y0, y1, k) + 10, 28, ctx.c("ink"), "semi", 0.85 * min(1, k * 3))


# ---------------------------------------------------------------- sahne çizerleri
def p_silfra(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#9FE3F0"), hexc("#1C5E86"))
    rays(canvas, r, t, 0.12, 6)
    gap = w * (0.20 + 0.04 * p)
    rng = np.random.default_rng(11)
    for side in (-1, 1):
        path = skia.Path()
        edge = x + w / 2 + side * gap / 2
        outer = x if side < 0 else x + w
        path.moveTo(outer, y)
        steps = 26
        for i in range(steps + 1):
            yy = y + h * i / steps
            jag = rng.uniform(0, 1) * 34 + 30 * math.sin(i * 0.9 + side)
            narrow = (i / steps) * w * 0.07
            path.lineTo(edge + side * (jag + narrow), yy)
        path.lineTo(outer, y + h)
        path.close()
        sh = skia.GradientShader.MakeLinear([skia.Point(0, y), skia.Point(0, y + h)],
                                            [hexc("#2A3B44"), hexc("#0A1217")])
        canvas.drawPath(path, skia.Paint(Shader=sh, AntiAlias=True))
        # yosun kenar çizgisi
        canvas.drawPath(path, paint("#7FD3C6", 0.25, stroke=3))
    start = 0.5 if h > 1500 else 0.25
    dy = y + h * (start + (0.75 - start) * 0.6 * ease(p))
    diver(canvas, x + w / 2 + 10 * math.sin(t), dy, 0.95 * min(w, h) / 700, hexc("#081219"),
          ang=1.25, kick=math.sin(t * 5))
    bubbles_up(canvas, x + w / 2 + 40, dy - 40, t, 10, 1, 0.7)
    if seg.get("id") == "i1":
        scale_bar(canvas, x + 40, y + 60, y + h - 60, ease_out((t - 0.6) / 2.2), "0 m", "100 m görüş", ctx)


def p_angelita(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#2E6C72"), hexc("#06120F"))
    rays(canvas, r, t, 0.07, 3)
    # mağara duvarları
    for side in (-1, 1):
        path = skia.Path()
        outer = x if side < 0 else x + w
        path.moveTo(outer, y)
        for i in range(21):
            yy = y + h * i / 20
            path.lineTo(x + w / 2 + side * (w * 0.36 + 25 * math.sin(i * 1.3 + side * 2)), yy)
        path.lineTo(outer, y + h)
        path.close()
        canvas.drawPath(path, paint("#0C1A1A"))
    cy = y + h * 0.62
    # bulut katmanı
    rng = np.random.default_rng(5)
    for i in range(70):
        bx = x + w * 0.12 + rng.random() * w * 0.76
        by = cy + 40 * math.sin(bx * 0.012 + t * 0.7) + rng.normal(0, 14)
        rr = 40 + rng.random() * 60
        canvas.drawOval(skia.Rect.MakeXYWH(bx - rr * 1.6, by - rr * 0.4, rr * 3.2, rr * 0.8),
                        paint("#E9F2EC", 0.10, blur=12))
    canvas.drawRect(skia.Rect.MakeXYWH(x, cy + 50, w, h), paint("#030807", 0.75))
    # kuru dallar
    for k, (bx, ln) in enumerate(((0.38, 180), (0.55, 230), (0.66, 140))):
        bp = skia.Path()
        sx = x + w * bx
        sy = cy + 60
        bp.moveTo(sx, sy)
        bp.lineTo(sx + 10, sy - ln)
        bp.moveTo(sx + 6, sy - ln * 0.55)
        bp.lineTo(sx + 50 * (1 if k % 2 else -1), sy - ln * 0.85)
        canvas.drawPath(bp, paint("#1B2A24", stroke=9, cap_round=True))
    # dalgıç feneri
    dx = x + w * 0.5 + 30 * math.sin(t * 0.5)
    dy = y + h * (0.22 + 0.12 * ease(p))
    cone = skia.Path()
    cone.moveTo(dx, dy + 20)
    cone.lineTo(dx - 120, cy + 10)
    cone.lineTo(dx + 120, cy + 10)
    cone.close()
    sh = skia.GradientShader.MakeLinear([skia.Point(0, dy), skia.Point(0, cy)],
                                        [skia.Color4f(1, 1, .9, .28), skia.Color4f(1, 1, .9, 0.02)])
    canvas.drawPath(cone, skia.Paint(Shader=sh, AntiAlias=True))
    diver(canvas, dx, dy, 0.7, hexc("#020606"), ang=1.4, kick=math.sin(t * 4))
    # derinlik etiketi
    canvas.drawLine(x + 30, cy, x + 120, cy, paint(ctx.c("accent"), stroke=3))
    text(canvas, "~30 m", x + 30, cy - 14, 30, ctx.c("accent"), "semi")


def coral(canvas, x, y, ang, ln, depth, g, color, rng):
    if depth == 0 or ln < 6:
        return
    ex = x + math.cos(ang) * ln * g
    ey = y - math.sin(ang) * ln * g
    canvas.drawLine(x, y, ex, ey, paint(color, stroke=max(3, depth * 3.2), cap_round=True))
    for d in (-0.45, 0.4):
        coral(canvas, ex, ey, ang + d + rng.uniform(-0.15, 0.15), ln * 0.72, depth - 1, g, color, rng)


def p_reef(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#3FA7B8"), hexc("#0D3550"))
    rays(canvas, r, t, 0.09, 4)
    g = ease_out(t / 1.6)
    cols = ["#E8846B", "#F2C48D", "#B48CD6", "#7FD3C6", "#F29BB0"]
    rng = np.random.default_rng(21)
    for i in range(9):
        cx = x + w * (0.05 + i * 0.11) + rng.uniform(-20, 20)
        coral(canvas, cx, y + h, math.pi / 2 + rng.uniform(-0.3, 0.3), 110 + rng.uniform(0, 70), 5,
              g, cols[i % 5], np.random.default_rng(i))
    for i in range(14):
        cx = x + rng.random() * w
        canvas.drawCircle(cx, y + h - 10, 30 * g + rng.random() * 30 * g, paint(cols[(i + 2) % 5], 0.9))
    # balık sürüsü
    for i in range(46):
        fx = x + w - ((t * (90 + (i % 5) * 25) + i * 61) % (w + 120)) + 60
        fy = y + h * 0.25 + (i * 37 % 220) + 12 * math.sin(t * 2 + i)
        fish_shape(canvas, fx, fy, 26 + (i % 3) * 6, ["#F2EBDD", "#F2C48D", "#7FD3C6"][i % 3], 0.85)
    # 4 kareden 3'ü
    k = ease_out((t - 0.8) / 1.5)
    for i in range(4):
        bx = x + 36 + i * 66
        by = y + 36
        canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, by, 54, 54), 8, 8),
                         paint(ctx.c("ink"), 0.6, stroke=3))
        if i < 3 and k > i / 3:
            canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx + 6, by + 6, 42, 42), 5, 5),
                             paint(ctx.c("accent"), min(1, (k - i / 3) * 3)))


def p_octopus(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#24414A"), hexc("#0B1416"))
    floor = y + h * 0.72
    canvas.drawRect(skia.Rect.MakeXYWH(x, floor, w, h), paint("#1A1612"))
    rng = np.random.default_rng(8)
    for i in range(260):
        canvas.drawCircle(x + rng.random() * w, floor + rng.random() * (h * 0.28), 1 + rng.random() * 2,
                          paint("#3A322A", 0.8))
    m = ease((t - 1.3) / 1.4)  # 0: ahtapot, 1: yassı balık
    cx, cy = x + w * 0.5, floor - 70 + 40 * m
    n_arm, pts = 8, 18
    for a_i in range(n_arm):
        base_ang = math.pi * (0.1 + 0.8 * a_i / (n_arm - 1))
        path = skia.Path()
        for j in range(pts):
            s = j / (pts - 1)
            # ahtapot kolu
            ang = base_ang + 0.6 * math.sin(t * 2.2 + a_i + s * 3) * s
            ox = cx + math.cos(ang) * (40 + 230 * s) * (1 if a_i < 4 else 1)
            oy = cy + 40 + math.sin(ang) * 30 * s + 60 * s * math.sin(a_i)
            # yassı balık kenarı (elips üstünde)
            phi = math.pi * (a_i / n_arm) + s * math.pi / n_arm
            if a_i % 2:
                phi = math.pi + phi
            fx = cx + math.cos(phi) * 260
            fy = cy + 50 + math.sin(phi) * 80
            px, py = lerp(ox, fx, m), lerp(oy, fy, m)
            if j == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        wdt = lerp(18, 26, m)
        canvas.drawPath(path, paint("#5A3B26", stroke=wdt, cap_round=True))
        dash = paint("#E9DCC4", 0.9, stroke=wdt * 0.6, cap_round=False)
        dash.setPathEffect(skia.DashPathEffect.Make([16, 16], (t * 20) % 32))
        canvas.drawPath(path, dash)
    head = skia.Rect.MakeXYWH(cx - lerp(55, 230, m), cy - lerp(90, 20, m), lerp(110, 460, m), lerp(130, 140, m))
    canvas.drawOval(head, paint(lerp_color("#6A4630", "#5C4A3A", m)))
    for k in range(5):
        canvas.drawOval(skia.Rect.MakeXYWH(cx - 40 + k * 18 - m * 160 + m * k * 70, cy - 60 + m * 60, 12, 28),
                        paint("#E9DCC4", 0.75))
    canvas.drawCircle(cx - 22 + m * 160, cy - 20 + m * 45, 7, paint("#F2EBDD"))
    canvas.drawCircle(cx + 22 + m * 160, cy - 20 + m * 45, 7, paint("#F2EBDD"))
    lab = "ahtapot" if m < 0.5 else "→ yassı balık taklidi"
    text(canvas, lab, x + 40, y + 70, 34, ctx.c("aqua"), "semi", 0.9)


def lerp_color(a, b, k):
    ca, cb = hexc(a), hexc(b)
    return skia.Color4f(lerp(ca.fR, cb.fR, k), lerp(ca.fG, cb.fG, k), lerp(ca.fB, cb.fB, k), 1)


def p_wreck(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#2F6E78"), hexc("#0A1E24"))
    rays(canvas, r, t, 0.06, 4)
    floor = y + h * 0.8
    canvas.drawRect(skia.Rect.MakeXYWH(x, floor, w, h), paint(ctx.c("sand"), 0.35))
    canvas.save()
    canvas.translate(x + w * 0.5, floor - 30)
    canvas.rotate(-9)
    hull = skia.Path()
    hull.moveTo(-380, -60)
    hull.lineTo(360, -60)
    hull.lineTo(300, 40)
    hull.lineTo(-330, 40)
    hull.close()
    canvas.drawPath(hull, paint("#13252B"))
    for bx, bw, bh in ((-180, 120, 80), (-40, 160, 120), (150, 90, 60)):
        canvas.drawRect(skia.Rect.MakeXYWH(bx, -60 - bh, bw, bh), paint("#16292F"))
    canvas.drawLine(40, -180, 60, -330, paint("#16292F", stroke=10))
    rng = np.random.default_rng(4)
    k = ease_out((t - 0.3) / 2.5)
    cols = ["#E8846B", "#B48CD6", "#F2C48D", "#7FD3C6"]
    for i in range(int(220 * k)):
        cxx = rng.uniform(-380, 360)
        cyy = -60 - rng.random() * 30 if rng.random() < 0.6 else rng.uniform(-180, -60)
        if cyy < -70 and not (-180 < cxx < 240):
            cyy = -60
        canvas.drawCircle(cxx, cyy, 3 + rng.random() * 7, paint(cols[i % 4], 0.9))
    canvas.restore()
    yr = int(lerp(1944, 2026, ease((t - 0.5) / 2.4)))
    text(canvas, str(yr), x + 40, y + 120, 96, ctx.c("ink"), "bold", 0.85)
    text(canvas, "1944 → bugün", x + 44, y + 166, 30, ctx.c("ink"), "semi", 0.8)


def hammerhead(canvas, x, y, s, color, a, t, i):
    canvas.save()
    canvas.translate(x, y)
    canvas.scale(s, s)
    p = paint(color, a)
    body = skia.Path()
    body.moveTo(-90, 0)
    body.cubicTo(-60, -22, 40, -18, 80, -2)
    body.lineTo(80, 2)
    body.cubicTo(40, 18, -60, 22, -90, 0)
    canvas.drawPath(body, p)
    canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(-104, -30, 18, 60), 8, 8), p)  # çekiç
    fin = skia.Path()
    fin.moveTo(-20, -14)
    fin.lineTo(6, -52)
    fin.lineTo(18, -12)
    fin.close()
    canvas.drawPath(fin, p)
    wag = 12 * math.sin(t * 5 + i)
    tail = skia.Path()
    tail.moveTo(76, 0)
    tail.lineTo(128, -36 + wag)
    tail.lineTo(112, 2)
    tail.lineTo(124, 24 + wag)
    tail.close()
    canvas.drawPath(tail, p)
    canvas.restore()


def p_sharks(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#2C7C9A"), hexc("#0A2233"))
    rays(canvas, r, t, 0.08, 5)
    for layer, (sc, al, sp, n) in enumerate(((0.45, 0.35, 40, 14), (0.75, 0.6, 70, 9), (1.25, 0.95, 110, 5))):
        rng = np.random.default_rng(layer + 30)
        visible = int(n * min(1, 0.35 + t / 2.5))
        for i in range(visible):
            off = rng.random() * (w + 400)
            sx = x + ((off + t * sp) % (w + 400)) - 200
            sy = y + h * (0.12 + 0.76 * rng.random())
            hammerhead(canvas, sx, sy, sc, "#08161E", al, t, i)


def p_sardines(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#2B6FA0"), hexc("#081B2E"))
    rays(canvas, r, t, 0.1, 5)
    key = ("sardines", int(w))
    if key not in ctx.cache:
        rng = np.random.default_rng(12)
        n = 2200
        ctx.cache[key] = (rng.normal(0, 1, (n, 3)), rng.random(n) * 6.28)
    pos, ph = ctx.cache[key]
    cx, cy = x + w * 0.5, y + h * 0.5
    R = min(w, h) * 0.32
    ang = t * 0.6
    px = pos[:, 0] * math.cos(ang) - pos[:, 2] * math.sin(ang)
    pz = pos[:, 0] * math.sin(ang) + pos[:, 2] * math.cos(ang)
    X = cx + px * R
    Y = cy + pos[:, 1] * R * 0.7
    # yunus geçişi: tünel aç
    dol = []
    for k, (t0, yy) in enumerate(((0.6, 0.42), (1.6, 0.6), (2.6, 0.48))):
        u = (t - t0) / 1.4
        if -0.2 < u < 1.2:
            dx_ = x - 150 + (w + 300) * u
            dy_ = y + h * yy + 30 * math.sin(u * 6)
            dol.append((dx_, dy_))
    for (dx_, dy_) in dol:
        d = np.hypot(X - dx_, Y - dy_) + 1e-3
        push = np.clip(150 - d, 0, None) / d
        X = X + (X - dx_) * push * 0.9
        Y = Y + (Y - dy_) * push * 0.9
    shimmer = 0.5 + 0.5 * np.sin(ph + t * 6 + pz * 2)
    for lvl in range(4):
        sel = (shimmer >= lvl / 4) & (shimmer < (lvl + 1) / 4 + (lvl == 3))
        pts = [skia.Point(float(a), float(b)) for a, b in zip(X[sel], Y[sel])]
        pp = paint("#DDE8EE", 0.35 + 0.18 * lvl, stroke=4.5, cap_round=True)
        canvas.drawPoints(skia.Canvas.kPoints_PointMode, pts, pp)
    for (dx_, dy_) in dol:
        dolphin(canvas, dx_, dy_, 1.0)


def dolphin(canvas, x, y, s):
    canvas.save()
    canvas.translate(x, y)
    canvas.scale(s, s)
    p = paint("#0D2230")
    b = skia.Path()
    b.moveTo(110, 0)
    b.cubicTo(80, -36, -40, -40, -110, -6)
    b.lineTo(-150, -30)
    b.lineTo(-140, 0)
    b.lineTo(-150, 30)
    b.lineTo(-110, 6)
    b.cubicTo(-40, 30, 80, 26, 110, 0)
    canvas.drawPath(b, p)
    f = skia.Path()
    f.moveTo(-10, -32)
    f.lineTo(-40, -70)
    f.lineTo(-50, -28)
    f.close()
    canvas.drawPath(f, p)
    canvas.drawRect(skia.Rect.MakeXYWH(108, -4, 30, 8), p)
    canvas.restore()


def p_tornado(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#5AA7C2"), hexc("#0E2E45"))
    rays(canvas, r, t, 0.14, 6)
    cx = x + w * 0.5
    n = 900
    rng = np.random.default_rng(9)
    base = rng.random(n) * 6.28
    hy = rng.random(n)
    rad = (60 + 230 * (0.4 + 0.6 * np.sin(hy * 3.0))) * (0.85 + 0.3 * rng.random(n))
    a = base + t * 1.4 + hy * 2.0
    X = cx + rad * np.cos(a) + 30 * np.sin(hy * 7 + t)
    Y = y + 40 + hy * (h - 80)
    depth = np.sin(a)
    order = np.argsort(depth)
    for i in order:
        d = depth[i]
        L = 22 + 10 * (d + 1)
        tx, ty = -math.sin(a[i]), 0.12
        col = "#E9EEF0" if d > 0 else "#7C9AA8"
        canvas.drawLine(float(X[i] - tx * L / 2), float(Y[i] - ty * L / 2), float(X[i] + tx * L / 2),
                        float(Y[i] + ty * L / 2), paint(col, 0.45 + 0.4 * (d + 1) / 2, stroke=3.2, cap_round=True))
    diver(canvas, cx + 10, y + h - 60, 0.55, hexc("#06121A"), ang=-0.15, kick=math.sin(t * 3))


def p_manta(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#3E8FB5"), hexc("#0B2638"))
    rays(canvas, r, t, 0.12, 5)
    cx = x + w * (0.45 + 0.1 * ease(p))
    cy = y + h * 0.36 + 12 * math.sin(t * 1.3)
    span = w * 0.42
    flap = 40 * math.sin(t * 1.6)
    m = skia.Path()
    m.moveTo(cx, cy - 70)
    m.cubicTo(cx + span * 0.4, cy - 80, cx + span * 0.8, cy - 40 + flap, cx + span, cy + 10 + flap)
    m.cubicTo(cx + span * 0.6, cy + 30, cx + span * 0.3, cy + 70, cx, cy + 80)
    m.cubicTo(cx - span * 0.3, cy + 70, cx - span * 0.6, cy + 30, cx - span, cy + 10 + flap)
    m.cubicTo(cx - span * 0.8, cy - 40 + flap, cx - span * 0.4, cy - 80, cx, cy - 70)
    canvas.drawPath(m, paint("#0A1A24"))
    for s_ in (-1, 1):
        canvas.drawOval(skia.Rect.MakeXYWH(cx + s_ * 40 - 14, cy - 110, 28, 60), paint("#0A1A24"))
    canvas.drawLine(cx, cy + 75, cx + 8, cy + 220, paint("#0A1A24", stroke=6, cap_round=True))
    diver(canvas, x + w * 0.5, y + h * 0.82, 0.42, hexc("#06121A"), ang=0.05, kick=math.sin(t * 3))
    bubbles_up(canvas, x + w * 0.5 + 20, y + h * 0.80, t, 8, 3, 0.6, 10, 160)
    k = ease_out((t - 0.8) / 1.2)
    ly = cy + 140
    canvas.drawLine(cx - span * k, ly, cx + span * k, ly, paint(ctx.c("accent"), stroke=3))
    for s_ in (-1, 1):
        canvas.drawLine(cx + s_ * span * k, ly - 12, cx + s_ * span * k, ly + 12, paint(ctx.c("accent"), stroke=3))
    if k > 0.6:
        text(canvas, "~7 m", cx, ly + 48, 38, ctx.c("accent"), "bold", (k - 0.6) / 0.4, "center")


def p_steps(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#3A7F90"), hexc("#0B2129"))
    rays(canvas, r, t, 0.08, 4)
    S = 62
    ox, oy = x + w * 0.5, y + h * 0.42
    heights = [[4, 4, 3, 3, 2], [4, 3, 3, 2, 1], [3, 3, 2, 1, 1], [3, 2, 1, 1, 0], [2, 1, 1, 0, 0]]
    top, left, right = hexc("#D9C7A3"), hexc("#8C7A5E"), hexc("#5E5140")
    for i in range(5):
        for j in range(5):
            hh = heights[i][j]
            for k in range(hh):
                idx = i + j + k
                g = ease_out((t - 0.15 * idx) / 0.6)
                if g <= 0:
                    continue
                px = ox + (j - i) * S * 0.87
                py = oy + (i + j) * S * 0.5 - k * S * g + (1 - g) * 120
                al = g
                tp = skia.Path()
                tp.moveTo(px, py - S * 0.5)
                tp.lineTo(px + S * 0.87, py)
                tp.lineTo(px, py + S * 0.5)
                tp.lineTo(px - S * 0.87, py)
                tp.close()
                lp = skia.Path()
                lp.moveTo(px - S * 0.87, py)
                lp.lineTo(px, py + S * 0.5)
                lp.lineTo(px, py + S * 1.5)
                lp.lineTo(px - S * 0.87, py + S)
                lp.close()
                rp = skia.Path()
                rp.moveTo(px + S * 0.87, py)
                rp.lineTo(px, py + S * 0.5)
                rp.lineTo(px, py + S * 1.5)
                rp.lineTo(px + S * 0.87, py + S)
                rp.close()
                for path_, col in ((lp, left), (rp, right), (tp, top)):
                    canvas.drawPath(path_, paint(col, al))
    q = ease((t - 2.0) / 0.8) * (1 - ease((t - 4.6) / 0.6))
    if q > 0:
        text(canvas, "?", x + w - 120, y + 200, 200, ctx.c("accent"), "bold", q, "center")


def world_map(canvas, ctx, r, a=1.0, land_col="#2A5560"):
    x, y, w, h = r
    key = ("map", x, y, w, h)
    if key not in ctx.cache:
        path = skia.Path()
        for poly in ctx.land:
            for i, (lo, la) in enumerate(poly):
                px, py = x + (lo + 180) / 360 * w, y + (90 - la) / 180 * h
                path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
            path.close()
        ctx.cache[key] = path
    canvas.save()
    canvas.clipRect(skia.Rect.MakeXYWH(x, y, w, h))
    canvas.drawPath(ctx.cache[key], paint(land_col, a))
    canvas.restore()


def geo(r, lonlat):
    x, y, w, h = r
    return x + (lonlat[0] + 180) / 360 * w, y + (90 - lonlat[1]) / 180 * h


def p_title(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#0F3D4C"), hexc("#07202A"))
    mh = w / 2
    mr = (x + 10, y + (h - mh) / 2, w - 20, mh - 20)
    world_map(canvas, ctx, mr, 0.9)
    for i, s in enumerate(ctx.sites):
        k = ease_out((t - 0.9 - i * 0.22) / 0.3)
        if k <= 0:
            continue
        gx, gy = geo(mr, s["lonlat"])
        canvas.drawCircle(gx, gy, 20 * k * (1.5 - 0.5 * k), paint(ctx.c("accent"), 0.25 * k))
        canvas.drawCircle(gx, gy, 7 * k, paint(ctx.c("accent")))
        text(canvas, f"{i + 1:02d}", gx + 10, gy - 12, 22, ctx.c("ink"), "semi", 0.85 * k)


def p_grid(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#0F3D4C"), hexc("#07202A"))
    world_map(canvas, ctx, (x, y + (h - w / 2) / 2, w, w / 2), 0.35)
    labels = ctx.ep.get("grid_labels", [])
    cw, ch_ = (w - 60) / 2, (h - 60) / 5
    for i, lab in enumerate(labels[:10]):
        col, row = i % 2, i // 2
        k = ease_out((t - i * 0.12) / 0.4)
        if k <= 0:
            continue
        bx = x + 20 + col * (cw + 20)
        by = y + 20 + row * (ch_ + 5) + (1 - k) * 30
        rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, by, cw, ch_ - 10), 16, 16)
        site = ctx.sites[i] if i < len(ctx.sites) else {}
        img = ctx.image(site.get("media", {}).get("_path"))
        if img is not None:
            canvas.save()
            canvas.clipRRect(rr, True)
            canvas.saveLayerAlpha(None, int(255 * k))
            draw_cover(canvas, img, (bx, by, cw, ch_ - 10), site["media"].get("focus", [0.5, 0.5]), 1.0 + 0.05 * t)
            canvas.drawRect(skia.Rect.MakeXYWH(bx, by, cw, ch_ - 10), paint(ctx.c("deep"), 0.55))
            canvas.restore()
            canvas.restore()
        else:
            canvas.drawRRect(rr, paint(ctx.c("deep"), 0.72 * k))
        name, rule = (lab.split(" · ") + [""])[:2]
        text(canvas, f"{i + 1:02d}", bx + 22, by + 52, 30, ctx.c("accent"), "bold", k)
        text(canvas, name, bx + 80, by + 52, 30, ctx.c("ink"), "semi", k)
        text(canvas, rule, bx + 80, by + 92, 26, ctx.c("aqua"), "med", k)


def p_end(canvas, ctx, r, t, p, seg):
    x, y, w, h = r
    water(canvas, r, hexc("#0F3D4C"), hexc("#06161F"))
    for i in range(10):
        col, row = i % 5, i // 5
        k = ease_out((t - i * 0.06) / 0.4)
        bx = x + 70 + col * (w - 140) / 4
        by = y + h * 0.38 + row * 140
        text(canvas, f"{i + 1:02d}", bx, by, 64, ctx.c("ink"), "bold", k * 0.9, "center")
        canvas.drawLine(bx - 30, by + 26, bx + 30, by + 26, paint(ctx.c("accent"), k, stroke=3))
    if int(t * 2) % 2 == 0:
        canvas.drawRect(skia.Rect.MakeXYWH(x + w / 2 - 3, y + h * 0.78, 6, 60), paint(ctx.c("accent")))


def p_typo(canvas, ctx, r, t, p, seg):
    """Genel tipografik sahne: yeni başlıklar için varsayılan."""
    x, y, w, h = r
    water(canvas, r, ctx.c("bg_top"), ctx.c("deep"))
    words = (seg.get("onscreen") or seg["text"]).split()
    lines = wrap(" ".join(words), 92, w - 120)
    for i, ln in enumerate(lines[:4]):
        k = ease_out((t - i * 0.15) / 0.5)
        text(canvas, ln, x + 60, y + h * 0.35 + i * 108 + (1 - k) * 40, 92, ctx.c("ink"), "bold", k)
    canvas.drawRect(skia.Rect.MakeXYWH(x + 60, y + h * 0.35 - 130, 120 * ease_out(t / 0.6), 10),
                    paint(ctx.c("accent")))


SAMPLING = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)


def draw_cover(canvas, img, r, focus=(0.5, 0.5), zoom=1.0, pan=(0.0, 0.0)):
    """Görseli r alanını dolduracak şekilde kırp (cover), odak noktası ve zoom ile."""
    x, y, w, h = r
    iw, ih = img.width(), img.height()
    s = max(w / iw, h / ih) * zoom
    sw, sh = w / s, h / s
    cx = min(max(iw * (focus[0] + pan[0]), sw / 2), iw - sw / 2)
    cy = min(max(ih * (focus[1] + pan[1]), sh / 2), ih - sh / 2)
    src = skia.Rect.MakeXYWH(cx - sw / 2, cy - sh / 2, sw, sh)
    canvas.drawImageRect(img, src, skia.Rect.MakeXYWH(x, y, w, h), SAMPLING)


def p_photo(canvas, ctx, r, t, p, seg):
    """Gerçek fotoğraf: Ken Burns (yavaş zoom + kaydırma), okunabilirlik için koyulaştırma."""
    m = seg["media"]
    img = ctx.image(m.get("_path"))
    if img is None:
        return PAINTERS.get(seg.get("scene"), p_typo)(canvas, ctx, r, t, p, seg)
    z0, z1 = m.get("zoom", [1.04, 1.14])
    k = ease(p) * 0.85 + p * 0.15
    pan = m.get("pan", [0.0, 0.0])
    x, y, w, h = r
    if m.get("fit") == "contain":
        # düşük çözünürlük / yatay kare: arkada bulanık geniş kopya, önde keskin görsel
        bp = skia.Paint(ImageFilter=skia.ImageFilters.Blur(30, 30))
        canvas.saveLayer(None, bp)
        draw_cover(canvas, img, r, m.get("focus", [0.5, 0.5]), 1.1)
        canvas.restore()
        canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint(ctx.c("deep"), 0.45))
        z = lerp(z0, z1, k)
        iw = PANEL[2] * z
        ih = iw * img.height() / img.width()
        cx, cy = x + w / 2 - 35, PANEL[1] + PANEL[3] / 2
        dst = skia.Rect.MakeXYWH(cx - iw / 2, cy - ih / 2, iw, ih)
        canvas.save()
        canvas.clipRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(PANEL[0], cy - min(ih, PANEL[3]) / 2, PANEL[2],
                                                                    min(ih, PANEL[3])), 24, 24), True)
        canvas.drawImageRect(img, dst, SAMPLING)
        canvas.restore()
    else:
        draw_cover(canvas, img, r, m.get("focus", [0.5, 0.5]), lerp(z0, z1, k), (pan[0] * k, pan[1] * k))
    canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint(ctx.c("deep"), 0.12))  # mat ton
    for y0, y1, a0, a1 in ((y, y + 560, 0.62, 0.0), (y + h * 0.56, y + h, 0.0, 0.82)):
        sh = skia.GradientShader.MakeLinear([skia.Point(0, y0), skia.Point(0, y1)],
                                            [skia.Color4f(0, 0.02, 0.04, a0), skia.Color4f(0, 0.02, 0.04, a1)])
        canvas.drawRect(skia.Rect.MakeXYWH(x, y0, w, y1 - y0), skia.Paint(Shader=sh))


PAINTERS = {"silfra": p_silfra, "angelita": p_angelita, "reef": p_reef, "octopus": p_octopus,
            "wreck": p_wreck, "sharks": p_sharks, "sardines": p_sardines, "tornado": p_tornado,
            "manta": p_manta, "steps": p_steps, "title": p_title, "grid": p_grid, "end": p_end,
            "typo": p_typo}


# ---------------------------------------------------------------- katmanlar
def scene_rect(sc, t):
    """Kanca sahnesi tam ekran; ilk liste öğesi tam ekrandan panele küçülür."""
    full = (0, 0, W, H)
    if sc.get("fullbleed") or sc["seg"].get("media", {}).get("_path"):
        return full
    if sc.get("zoom_from_full"):
        k = ease((t - 0.05) / 0.7)
        return tuple(lerp(a, b, k) for a, b in zip(full, PANEL))
    return PANEL


def draw_scene(canvas, ctx, sc, t, alpha):
    seg = sc["seg"]
    r = scene_rect(sc, t)
    p = min(1, max(0, t / max(0.1, sc["dur"])))
    canvas.saveLayerAlpha(None, int(255 * alpha))
    rr = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(*r), 28 if r != (0, 0, W, H) else 0, 28 if r != (0, 0, W, H) else 0)
    canvas.save()
    canvas.clipRRect(rr, True)
    if seg.get("media", {}).get("_path"):
        p_photo(canvas, ctx, r, t, p, seg)
    else:
        PAINTERS.get(sc["scene"], p_typo)(canvas, ctx, r, t, p, seg)
    canvas.restore()
    if r == (0, 0, W, H) and "lonlat" in seg:
        draw_minimap(canvas, ctx, sc, t, (0, 200, W - 50, 0), small=True)
    if r != (0, 0, W, H):
        canvas.drawRRect(rr, paint(ctx.c("ink"), 0.12, stroke=2))
        if "lonlat" in seg:
            draw_minimap(canvas, ctx, sc, t, r)
    draw_header(canvas, ctx, sc, t)
    canvas.restore()


def draw_minimap(canvas, ctx, sc, t, r, small=False):
    mw, mh = (220, 110) if small else (250, 125)
    mx, my = r[0] + r[2] - mw - 18, r[1] + 18
    canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(mx - 8, my - 8, mw + 16, mh + 16), 12, 12),
                     paint(ctx.c("deep"), 0.7))
    world_map(canvas, ctx, (mx, my, mw, mh), 0.95, "#3F6E76")
    prev = sc.get("prev_lonlat") or sc["seg"]["lonlat"]
    k = ease_out((t - 0.1) / 0.6)
    lon = lerp(prev[0], sc["seg"]["lonlat"][0], k)
    lat = lerp(prev[1], sc["seg"]["lonlat"][1], k)
    gx, gy = geo((mx, my, mw, mh), (lon, lat))
    pulse = (t * 1.2) % 1
    canvas.drawCircle(gx, gy, 6 + 16 * pulse, paint(ctx.c("accent"), 0.5 * (1 - pulse), stroke=2))
    canvas.drawCircle(gx, gy, 6, paint(ctx.c("accent")))


def draw_header(canvas, ctx, sc, t):
    seg = sc["seg"]
    k = ease_out((t - 0.05) / 0.45)
    dy = (1 - k) * 24
    if "place" in seg:
        n = sc["index"]
        text(canvas, f"{n:02d} / {sc['total']:02d}", 80, 262 + dy, 34, ctx.c("accent"), "bold", k)
        text(canvas, seg["country"].upper(), 80 + text_width(f"{n:02d} / {sc['total']:02d}", 34) + 26, 262 + dy,
             28, ctx.c("muted"), "semi", k)
        size = 84 if text_width(seg["place"], 84) < 820 else 68
        text(canvas, seg["place"], 76, 360 + dy, size, ctx.c("ink"), "bold", k)
        rep = seg.get("media", {}).get("representative")
        if rep and seg["media"].get("_path"):
            lab = f"TEMSİLÎ GÖRÜNTÜ · {rep}"
            tw = text_width(lab, 24, "semi")
            canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(78, 388 + dy, tw + 28, 38), 19, 19),
                             paint("#000000", 0.45 * k))
            text(canvas, lab, 92, 415 + dy, 24, ctx.c("sand"), "semi", k)
        # bilgi bloğu panelin altında
        fy = PANEL[1] + PANEL[3] + 92
        fs = 66 if text_width(seg["onscreen"], 66) < 860 else 52
        k2 = ease_out((t - 0.35) / 0.5)
        text(canvas, seg["onscreen"], 76, fy + (1 - k2) * 20, fs, ctx.c("ink"), "bold", k2)
        text(canvas, seg.get("kicker", ""), 78, fy + 56 + (1 - k2) * 20, 32, ctx.c("aqua"), "med", k2 * 0.95)
    elif sc.get("fullbleed"):
        lines = wrap(seg["onscreen"], 92, 860)
        for i, ln in enumerate(lines):
            font = skia.Font(TF["bold"], 92)
            sh = paint("#000000", 0.55 * k, blur=10)
            canvas.drawString(ln, 84, 336 + i * 104 + dy, font, sh)
            text(canvas, ln, 80, 330 + i * 104 + dy, 92, "#F7FBFC", "bold", k)
    else:
        if seg.get("kicker"):
            text(canvas, seg["kicker"].upper(), 80, 262 + dy, 30, ctx.c("accent"), "bold", k)
        lines = wrap(seg["onscreen"], 70, 860)
        for i, ln in enumerate(lines[:2]):
            text(canvas, ln, 76, 345 + i * 82 + dy, 70, ctx.c("ink"), "bold", k)
    if seg.get("_credit"):
        text(canvas, seg["_credit"], 80, 1372, 21, "#FFFFFF", "reg", 0.72 * k)


def draw_caption(canvas, ctx, cues, tt):
    cue = next((c for c in cues if c["start"] <= tt < c["end"]), None)
    if not cue:
        return
    k = ease_out((tt - cue["start"]) / 0.12)
    size = 46
    lines = cue["lines"]
    cy = 1470
    maxw = max(text_width(l, size, "tsemi") for l in lines)
    bh = len(lines) * 60 + 28
    bx = 470 - maxw / 2 - 26
    canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(bx, cy - 52, maxw + 52, bh), 18, 18),
                     paint("#03090D", 0.62 * k))
    for i, ln in enumerate(lines):
        text(canvas, ln, 470, cy + i * 60, size, "#FFFFFF", "tsemi", k, "center")


# ---------------------------------------------------------------- ana döngü
def render(ep, timeline, cues, audio_path, out_path, scale=1.0, frames_limit=None):
    ctx = Ctx(ep, timeline)
    sw, sh = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2
    surface = skia.Surface(sw, sh)
    canvas = surface.getCanvas()
    nframes = int(round(timeline["duration"] * FPS))
    if frames_limit:
        nframes = min(nframes, frames_limit)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra",
           "-s", f"{sw}x{sh}", "-r", str(FPS), "-i", "-"]
    if audio_path:
        cmd += ["-i", audio_path, "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    scenes = timeline["scenes"]
    for f in range(nframes):
        tt = f / FPS
        canvas.save()
        canvas.scale(scale, scale)
        draw_bg(canvas, ctx, f)
        idx = max(i for i, s in enumerate(scenes) if s["start"] <= tt + 1e-6)
        sc = scenes[idx]
        lt = tt - sc["start"]
        if idx > 0 and lt < XFADE:
            pv = scenes[idx - 1]
            draw_scene(canvas, ctx, pv, tt - pv["start"], 1 - ease(lt / (XFADE * 0.6)))
            draw_scene(canvas, ctx, sc, lt, ease(lt / XFADE))
        else:
            draw_scene(canvas, ctx, sc, lt, 1.0)
        # sonda kararma
        if tt > timeline["duration"] - 0.6:
            canvas.drawRect(skia.Rect.MakeWH(W, H), paint("#000000", ease((tt - timeline["duration"] + 0.6) / 0.6)))
        draw_caption(canvas, ctx, cues, tt)
        draw_grain(canvas, ctx, f)
        canvas.restore()
        proc.stdin.write(surface.makeImageSnapshot().tobytes())
        if f % 150 == 0:
            print(f"  kare {f}/{nframes}", flush=True)
    proc.stdin.close()
    proc.wait()
    if proc.returncode:
        raise RuntimeError("ffmpeg başarısız")


def still(ep, timeline, cues, t, out_png):
    """Tek kare PNG (kontrol için)."""
    ctx = Ctx(ep, timeline)
    surface = skia.Surface(W, H)
    canvas = surface.getCanvas()
    f = int(t * FPS)
    draw_bg(canvas, ctx, f)
    scenes = timeline["scenes"]
    idx = max(i for i, s in enumerate(scenes) if s["start"] <= t + 1e-6)
    draw_scene(canvas, ctx, scenes[idx], t - scenes[idx]["start"], 1.0)
    draw_caption(canvas, ctx, cues, t)
    draw_grain(canvas, ctx, f)
    surface.makeImageSnapshot().save(out_png, skia.kPNG)
