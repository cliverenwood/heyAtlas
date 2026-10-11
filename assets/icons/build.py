import math, os, cairosvg
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC, OUT = f"{ROOT}/src", f"{ROOT}/out"

def ring_path(cx, cy, r, theta, g):
    """Kreisring mit Lücke bei Winkel theta (Grad, SVG-Koordinaten), halbe Lückenbreite g."""
    a0, a1 = math.radians(theta + g), math.radians(theta - g + 360)
    p = lambda a: (cx + r*math.cos(a), cy + r*math.sin(a))
    (x0, y0), (x1, y1) = p(a0), p(a1)
    return f"M{x0:.2f} {y0:.2f} A{r} {r} 0 1 1 {x1:.2f} {y1:.2f}"

def pt(cx, cy, r, theta):
    a = math.radians(theta); return cx + r*math.cos(a), cy + r*math.sin(a)

# ---------- 1) App-/Dock-Icon (1024) ----------
R, SW, TH, G = 270, 30, -45, 14
dx, dy = pt(512, 512, R, TH)
bars = "".join(
    f'<rect x="{512+off-30}" y="{512-h/2}" width="60" height="{h}" rx="30" fill="url(#bar)"/>'
    for off, h in [(-184,130),(-92,250),(0,380),(92,250),(184,130)])
app = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<defs>
 <linearGradient id="bg" x1="0.15" y1="0" x2="0.85" y2="1">
  <stop offset="0" stop-color="#5A5FEA"/><stop offset="0.55" stop-color="#2A2E96"/><stop offset="1" stop-color="#121641"/></linearGradient>
 <radialGradient id="hl" cx="0.3" cy="0.12" r="0.75">
  <stop offset="0" stop-color="#fff" stop-opacity="0.22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
 <linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#C9CEFF"/></linearGradient>
 <radialGradient id="glow"><stop offset="0" stop-color="#FFB84D" stop-opacity="0.55"/><stop offset="1" stop-color="#FFB84D" stop-opacity="0"/></radialGradient>
</defs>
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/>
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#hl)"/>
<path d="{ring_path(512,512,R,TH,G)}" fill="none" stroke="#fff" stroke-width="{SW}" stroke-linecap="round" opacity="0.96"/>
<circle cx="{dx:.1f}" cy="{dy:.1f}" r="78" fill="url(#glow)"/>
<circle cx="{dx:.1f}" cy="{dy:.1f}" r="34" fill="#FFB84D"/>
{bars}
</svg>'''
open(f"{SRC}/app-icon.svg", "w").write(app)

# ---------- 2) Menüleisten-Icons (Template: schwarz + Alpha, 36er Raster = @2x) ----------
tx, ty = pt(18, 18, 14.5, TH)
tbars = lambda fill: "".join(f'<rect x="{x-2}" y="{18-h/2}" width="4" height="{h}" rx="2" fill="{fill}"/>'
                              for x, h in [(11,9),(18,18),(25,9)])
idle = f'''<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
<path d="{ring_path(18,18,14.5,TH,17)}" fill="none" stroke="#000" stroke-width="3" stroke-linecap="round"/>
<circle cx="{tx:.2f}" cy="{ty:.2f}" r="2.6" fill="#000"/>
{tbars('#000')}</svg>'''
def rrect(x, y, w, h, r):
    return (f"M{x+r} {y} H{x+w-r} A{r} {r} 0 0 1 {x+w} {y+r} V{y+h-r} A{r} {r} 0 0 1 {x+w-r} {y+h} "
            f"H{x+r} A{r} {r} 0 0 1 {x} {y+h-r} V{y+r} A{r} {r} 0 0 1 {x+r} {y} Z")
holes = " ".join(rrect(x-2, 18-h/2, 4, h, 2) for x, h in [(11,9),(18,18),(25,9)])
disc = "M2 18 A16 16 0 1 1 34 18 A16 16 0 1 1 2 18 Z"
listening = f'''<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
<path d="{disc} {holes}" fill="#000" fill-rule="evenodd"/></svg>'''
open(f"{SRC}/menubar-idle.svg", "w").write(idle)
open(f"{SRC}/menubar-listening.svg", "w").write(listening)

# ---------- 3) DMG-Hintergrund (660x400, hell – Finder-Beschriftungen sind schwarz) ----------
ax0, ax1, ay = 262, 398, 200
dmg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="660" height="400" viewBox="0 0 660 400">
<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F6F6FF"/><stop offset="1" stop-color="#DCDFFF"/></linearGradient>
 <radialGradient id="g1" cx="0.12" cy="0.05" r="0.7"><stop offset="0" stop-color="#8E93FF" stop-opacity="0.35"/><stop offset="1" stop-color="#8E93FF" stop-opacity="0"/></radialGradient>
</defs>
<rect width="660" height="400" fill="url(#bg)"/><rect width="660" height="400" fill="url(#g1)"/>
<g font-family="Inter, Helvetica, Arial, sans-serif" text-anchor="middle">
 <text x="330" y="62" font-size="30" font-weight="700" fill="#161A52" letter-spacing="-0.5">Hey Atlas</text>
 <text x="330" y="86" font-size="14" fill="#5A60C8">Dein Sprachassistent</text>
 <text x="330" y="352" font-size="14" fill="#3E4490">Zum Installieren das Symbol auf „Programme“ ziehen</text>
</g>
<path d="M{ax0} {ay} H{ax1-8}" stroke="#4B50D8" stroke-width="4" stroke-linecap="round" stroke-dasharray="2 10" fill="none"/>
<path d="M{ax1-18} {ay-14} L{ax1} {ay} L{ax1-18} {ay+14}" stroke="#4B50D8" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
</svg>'''
open(f"{SRC}/dmg-background.svg", "w").write(dmg)

# ---------- Rendern ----------
def render(svg, png, w, h=None):
    cairosvg.svg2png(url=svg, write_to=png, output_width=w, output_height=h or w)

# App-Icon: 1024 + weicher Schatten (macOS-Vorlage), dann alle Größen
render(f"{SRC}/app-icon.svg", f"{OUT}/_app_raw.png", 1024)
raw = Image.open(f"{OUT}/_app_raw.png").convert("RGBA")
a = raw.getchannel("A").filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v*0.38))
shadow = Image.new("RGBA", raw.size, (10, 12, 40, 0)); shadow.putalpha(a)
shadow = Image.alpha_composite(Image.new("RGBA", raw.size, (0,0,0,0)),
                               shadow.transform(raw.size, Image.AFFINE, (1,0,0,0,1,-12)))
app_png = Image.alpha_composite(shadow, raw)
os.remove(f"{OUT}/_app_raw.png")
app_png.save(f"{OUT}/HeyAtlas-AppIcon-1024.png")

iset = f"{OUT}/HeyAtlas.iconset"; os.makedirs(iset, exist_ok=True)
for s in (16, 32, 128, 256, 512):
    app_png.resize((s, s), Image.LANCZOS).save(f"{iset}/icon_{s}x{s}.png")
    app_png.resize((s*2, s*2), Image.LANCZOS).save(f"{iset}/icon_{s}x{s}@2x.png")
# iconutil statt Pillow: Pillow lässt in der .icns die Größen 16x16 und 32x32 (1x) weg
import shutil, subprocess
subprocess.run(["iconutil", "-c", "icns", iset, "-o", f"{OUT}/HeyAtlas.icns"], check=True)  # App-Icon (Dock/Finder)
shutil.copy(f"{OUT}/HeyAtlas.icns", f"{OUT}/VolumeIcon.icns")                             # Symbol des gemounteten DMG

# Menüleiste
mb = f"{OUT}/MenuBar"; os.makedirs(mb, exist_ok=True)
for name, svg in (("AtlasIdle", "menubar-idle"), ("AtlasListening", "menubar-listening")):
    render(f"{SRC}/{svg}.svg", f"{mb}/{name}Template.png", 18)
    render(f"{SRC}/{svg}.svg", f"{mb}/{name}Template@2x.png", 36)
    render(f"{SRC}/{svg}.svg", f"{mb}/{name}Template@3x.png", 54)

# DMG-Hintergrund
render(f"{SRC}/dmg-background.svg", f"{OUT}/dmg-background.png", 660, 400)
render(f"{SRC}/dmg-background.svg", f"{OUT}/dmg-background@2x.png", 1320, 800)
print("fertig")
