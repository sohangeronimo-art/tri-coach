"""Build docs/ (the GitHub Pages site) from app/app.html, plus an optional demo preview.

  python tools/build_site.py                 # writes docs/index.html, manifest, sw.js, icons
  python tools/build_site.py --demo bundle.json --preview out.html
"""
import argparse, json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#EDF1F0" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0E1617" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Brick">
<meta name="robots" content="noindex">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<link rel="icon" href="icons/icon-192.png">
<style>:root{padding-top:env(safe-area-inset-top,0px)}body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>
"""

SW = """const CACHE = "brick-v%s";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/apple-touch-icon.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // Training data and the page itself: network first, cached copy when offline.
  if (url.origin === location.origin) {
    e.respondWith(fetch(e.request).then(r => {
      const copy = r.clone();
      if (r.ok) caches.open(CACHE).then(c => c.put(url.pathname.endsWith("data.enc") ? "data.enc" : e.request, copy));
      return r;
    }).catch(() => caches.match(url.pathname.endsWith("data.enc") ? "data.enc" : e.request)
      .then(r => r || caches.match("index.html"))));
    return;
  }
  // Fonts: cache first.
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(res => {
    const copy = res.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return res; })));
});
"""

MANIFEST = {
    "name": "Brick Coach", "short_name": "Brick", "start_url": "./#today", "scope": "./",
    "display": "standalone", "background_color": "#EDF1F0", "theme_color": "#1F4E5A",
    "icons": [{"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}


def icons(out):
    from PIL import Image, ImageDraw
    os.makedirs(out, exist_ok=True)
    for size, name, rounded in ((512, "icon-512.png", False), (192, "icon-192.png", False), (180, "apple-touch-icon.png", False)):
        S = 1024
        im = Image.new("RGB", (S, S), "#1F4E5A")
        d = ImageDraw.Draw(im)
        h, gap, w = 150, 60, 520
        y0 = (S - (3 * h + 2 * gap)) // 2
        x0 = (S - (w + 170)) // 2
        for i, (col, dx) in enumerate((("#46B8C2", 0), ("#E2AA3F", 170), ("#E5775F", 0))):
            y = y0 + i * (h + gap)
            d.rounded_rectangle([x0 + dx, y, x0 + dx + w, y + h], radius=38, fill=col)
        im.resize((size, size), Image.LANCZOS).save(os.path.join(out, name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo")
    ap.add_argument("--preview")
    a = ap.parse_args()
    src = open(os.path.join(ROOT, "app", "app.html")).read()
    head, body = src.split('<div id="app"></div>', 1)
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    with open(os.path.join(docs, "index.html"), "w") as f:
        f.write(HEAD + head + "</head>\n<body>\n<div id=\"app\"></div>" + body + "\n</body>\n</html>\n")
    import hashlib
    ver = hashlib.sha1(src.encode()).hexdigest()[:8]
    open(os.path.join(docs, "sw.js"), "w").write(SW % ver)
    json.dump(MANIFEST, open(os.path.join(docs, "manifest.webmanifest"), "w"), indent=1)
    open(os.path.join(docs, ".nojekyll"), "w").close()
    icons(os.path.join(docs, "icons"))
    if a.demo and a.preview:
        data = open(a.demo).read().replace("</", "<\\/")
        demo = src.replace('<div id="app"></div>', '<div id="app"></div>\n<script>window.__DEMO__ = ' + data + ';</script>', 1)
        open(a.preview, "w").write(demo)
    print("built docs/", ver)


if __name__ == "__main__":
    main()
