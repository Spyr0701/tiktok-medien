"""Instagram zu TikTok: neue Instagram-Beitraege und Stories als ENTWURF in den TikTok-Posteingang laden.

Nichts geht automatisch online. TikTok meldet in der App einen neuen Entwurf, Spyro waehlt Sound und
Text und veroeffentlicht selbst. Instagram wird nur gelesen (META_IG_TOKEN, wie marketingreport/instagram.py).

Aufrufe (aus diesem Ordner oder mit vollem Pfad):
  python tiktok_sync.py anmelden      einmalig: TikTok-Freigabe im Browser, Token wird lokal gespeichert
  python tiktok_sync.py probe         Trockenlauf: zeigt und baut, was hochginge, ohne TikTok-Aufruf
  python tiktok_sync.py               normaler Lauf (Zeitplan zweimal taeglich)
  python tiktok_sync.py status        zeigt die letzten Uebertragungen

Geheimnisse:
  TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET   Windows-Umgebungsvariablen (Sandbox „Instagram zu TikTok“)
  Token-Datei                               %LOCALAPPDATA%\\Ampullarium\\tiktok_token.json (nie im Projektordner)
Keine Ausgabe enthaelt Token, Code oder Secret.
"""
import datetime as dt
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import webbrowser

import requests

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "marketingreport"))
import instagram  # noqa: E402  (nur _get, nur lesend)

# Pfade lassen sich per Umgebungsvariable umlegen (GitHub-Routine, siehe medien_repo/.github/workflows/tiktok.yml)
DATEN = os.environ.get("TIKTOK_DATEN") or os.path.join(HIER, "daten")
AUSGANG = os.environ.get("TIKTOK_AUSGANG") or os.path.join(HIER, "ausgang")
STAND = os.path.join(DATEN, "uebertragen.json")
LOG = os.path.join(DATEN, "logs", "lauf.log")
POSTS_CSV = os.environ.get("TIKTOK_POSTS_CSV") or os.path.join(HIER, "..", "marketingreport", "eingang", "posts.csv")
TOKEN_DATEI = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Ampullarium", "tiktok_token.json")

REDIRECT_URI = "https://ampullarium.com/"  # muss exakt so im Login Kit der Sandbox stehen
SCOPES = "user.info.basic,video.upload"
API = "https://open.tiktokapis.com/v2"

STORIES_MITNEHMEN = True       # Spyro 05.11.2026: Stories auch rueber
MAX_JE_LAUF = 5                # TikTok erlaubt nur wenige offene Entwuerfe je 24 Stunden
SEKUNDEN_JE_BILD = 4           # Karussell als Video: Lesezeit je Folie
SEKUNDEN_STORY_BILD = 6
AUSLASSEN_TYPEN = {"angeheftet", "werbung"}  # posts.csv typ (Profil-Aesthetik, Werbe-Content)


class Fehler(Exception):
    pass


def log(text):
    zeile = f"{dt.datetime.now():%Y-%m-%d %H:%M} {text}"
    print(zeile)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(zeile + "\n")


# ---------------------------------------------------------------- TikTok Anmeldung

def _client():
    k, s = os.environ.get("TIKTOK_CLIENT_KEY", "").strip(), os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not k or not s:
        raise Fehler("TIKTOK_CLIENT_KEY oder TIKTOK_CLIENT_SECRET fehlt (Windows-Umgebungsvariable, danach Terminal neu öffnen).")
    return k, s


def _token_speichern(antwort):
    if "access_token" not in antwort:
        raise Fehler(f"TikTok hat keinen Token geliefert: {antwort.get('error')} {antwort.get('error_description')}")
    jetzt = time.time()
    daten = {
        "access_token": antwort["access_token"],
        "laeuft_ab": jetzt + int(antwort.get("expires_in", 0)) - 300,
        "refresh_token": antwort["refresh_token"],
        "refresh_laeuft_ab": jetzt + int(antwort.get("refresh_expires_in", 0)),
        "open_id": antwort.get("open_id"),
        "scope": antwort.get("scope"),
    }
    os.makedirs(os.path.dirname(TOKEN_DATEI), exist_ok=True)
    with open(TOKEN_DATEI, "w", encoding="utf-8") as f:
        json.dump(daten, f)
    return daten


STATE_DATEI = os.path.join(os.path.dirname(TOKEN_DATEI), "tiktok_state.txt")


def _auth_url():
    key, _ = _client()
    state = secrets.token_urlsafe(16)
    os.makedirs(os.path.dirname(STATE_DATEI), exist_ok=True)
    with open(STATE_DATEI, "w", encoding="utf-8") as f:
        f.write(state)
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": key, "scope": SCOPES, "response_type": "code", "redirect_uri": REDIRECT_URI, "state": state})


def anmelden():
    url = _auth_url()
    print("Im Browser öffnet sich TikTok. Mit @ampullarium anmelden und „Autorisieren“ tippen.")
    print("Danach landest du auf ampullarium.com. Kopiere die komplette Adresse aus der Adresszeile")
    print("(beginnt mit https://ampullarium.com/?code=...) und füge sie hier ein.\n")
    webbrowser.open(url)
    anmelden_fertig(input("Adresse: ").strip())


def anmelden_fertig(zurueck):
    """Tauscht den Code aus der Rueckleitungs-Adresse gegen den Token (auch ohne Terminal-Eingabe nutzbar)."""
    key, secret = _client()
    with open(STATE_DATEI, encoding="utf-8") as f:
        state = f.read().strip()
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(zurueck).query)
    if q.get("state", [""])[0] != state:
        raise Fehler("Die Adresse passt nicht zu dieser Anmeldung (state). Bitte neu starten.")
    if "code" not in q:
        raise Fehler(f"Kein Code in der Adresse. TikTok meldet: {q.get('error', ['?'])[0]}")
    r = requests.post(f"{API}/oauth/token/", timeout=30, data={
        "client_key": key, "client_secret": secret, "code": q["code"][0],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI})
    d = _token_speichern(r.json())
    print(f"\nFertig. Rechte: {d['scope']}. Gültig bis {dt.datetime.fromtimestamp(d['refresh_laeuft_ab']):%d.%m.%Y}.")


def _access_token():
    rt = os.environ.get("TIKTOK_REFRESH_TOKEN", "").strip()
    if rt:  # GitHub-Routine: kein Token auf der Platte, jeder Lauf holt einen frischen Zugang
        key, secret = _client()
        a = requests.post(f"{API}/oauth/token/", timeout=30, data={
            "client_key": key, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": rt}).json()
        if "access_token" not in a:
            raise Fehler(f"TikTok-Anmeldung abgelehnt ({a.get('error')}). Lokal neu anmelden und das Secret "
                         "TIKTOK_REFRESH_TOKEN im Repo erneuern.")
        if a.get("refresh_token") and a["refresh_token"] != rt:
            log("WARNUNG: TikTok hat einen neuen Refresh-Token ausgegeben. Secret TIKTOK_REFRESH_TOKEN erneuern, "
                "sonst scheitert ein späterer Lauf.")
        return a["access_token"]
    if not os.path.exists(TOKEN_DATEI):
        raise Fehler("Noch keine TikTok-Anmeldung. Einmal ausführen: python tiktok_sync.py anmelden")
    with open(TOKEN_DATEI, encoding="utf-8") as f:
        d = json.load(f)
    if time.time() < d["laeuft_ab"]:
        return d["access_token"]
    if time.time() > d.get("refresh_laeuft_ab", 0):
        raise Fehler("TikTok-Anmeldung abgelaufen. Neu ausführen: python tiktok_sync.py anmelden")
    key, secret = _client()
    r = requests.post(f"{API}/oauth/token/", timeout=30, data={
        "client_key": key, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": d["refresh_token"]})
    return _token_speichern(r.json())["access_token"]


# ---------------------------------------------------------------- Instagram lesen

def _konto_id():
    seiten = instagram._get("me/accounts", {"fields": "instagram_business_account{id,username}"})
    k = next((s["instagram_business_account"] for s in seiten.get("data", []) if s.get("instagram_business_account")), None)
    if not k:
        raise Fehler("Kein Instagram-Konto über die Seite erreichbar.")
    return k["id"]


def _auslassen_links():
    import csv
    raus = set()
    if os.path.exists(POSTS_CSV):
        with open(POSTS_CSV, encoding="utf-8") as f:
            for z in csv.DictReader(f):
                if (z.get("typ") or "").strip().lower() in AUSLASSEN_TYPEN:
                    raus.add((z.get("link") or "").strip().rstrip("/").replace("/reel/", "/p/"))
    return raus


def instagram_neu(ab):
    """Beitraege und Stories nach dem Zeitpunkt ab (UTC), aelteste zuerst."""
    konto = _konto_id()
    felder = "id,timestamp,media_type,media_product_type,permalink,caption,media_url,children{media_type,media_url}"
    liste = []
    antwort = instagram._get(f"{konto}/media", {"fields": felder, "limit": 25})
    for m in antwort.get("data", []):
        liste.append({**m, "art": "beitrag"})
    if STORIES_MITNEHMEN:
        for s in instagram._get(f"{konto}/stories", {"fields": "id,timestamp,media_type,media_url,permalink"}).get("data", []):
            liste.append({**s, "art": "story"})
    raus = _auslassen_links()
    neu = []
    for m in liste:
        zeit = dt.datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if zeit <= ab:
            continue
        link = (m.get("permalink") or "").rstrip("/").replace("/reel/", "/p/")
        if m["art"] == "beitrag" and link in raus:
            continue
        m["_zeit"] = zeit
        neu.append(m)
    return sorted(neu, key=lambda m: m["_zeit"])


# ---------------------------------------------------------------- Datei fuer TikTok bauen

def _laden(url, ziel):
    with requests.get(url, stream=True, timeout=120) as r:  # Instagram-CDN, ohne Token
        r.raise_for_status()
        with open(ziel, "wb") as f:
            for teil in r.iter_content(1 << 20):
                f.write(teil)


def _ffmpeg(args):
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if p.returncode:
        raise Fehler("ffmpeg: " + p.stderr.strip()[-300:])


# 9:16, Bild mittig, unscharfer Hintergrund aus demselben Bild, stilles Tonsignal (Sound waehlt Spyro in TikTok)
_FILTER = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
           "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
           "[bg][fg]overlay=(W-w)/2:(H-h)/2,scale=out_range=tv,format=yuv420p,fps=30[v]")


# Standard-H.264 wie vom Handy: begrenzter Farbbereich (JPEG liefert sonst yuvj420p), Ton AAC, Index vorne
_VIDEO_OPT = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
              "-color_range", "tv", "-c:a", "aac", "-ar", "44100", "-shortest", "-movflags", "+faststart"]


def _bild_zu_video(bild, ziel, sekunden):
    _ffmpeg(["-loop", "1", "-t", str(sekunden), "-i", bild, "-f", "lavfi", "-t", str(sekunden),
             "-i", "anullsrc=r=44100:cl=stereo", "-filter_complex", _FILTER, "-map", "[v]", "-map", "1:a",
             *_VIDEO_OPT, ziel])


def datei_bauen(m, ordner):
    """Liefert den Pfad einer MP4, die TikTok als Entwurf annimmt."""
    os.makedirs(ordner, exist_ok=True)
    typ = m.get("media_type")
    if typ == "VIDEO":
        ziel = os.path.join(ordner, "video.mp4")
        _laden(m["media_url"], ziel)
        return ziel
    if typ == "IMAGE":
        bild = os.path.join(ordner, "bild.jpg")
        _laden(m["media_url"], bild)
        ziel = os.path.join(ordner, "video.mp4")
        _bild_zu_video(bild, ziel, SEKUNDEN_STORY_BILD if m["art"] == "story" else SEKUNDEN_JE_BILD * 2)
        return ziel
    if typ == "CAROUSEL_ALBUM":
        teile = []
        for i, c in enumerate((m.get("children") or {}).get("data", []), 1):
            if c.get("media_type") == "VIDEO":
                roh = os.path.join(ordner, f"teil{i:02d}_roh.mp4")
                _laden(c["media_url"], roh)
                t = os.path.join(ordner, f"teil{i:02d}.mp4")
                _ffmpeg(["-i", roh, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-filter_complex", _FILTER,
                         "-map", "[v]", "-map", "1:a", *_VIDEO_OPT, t])
            else:
                bild = os.path.join(ordner, f"teil{i:02d}.jpg")
                _laden(c["media_url"], bild)
                t = os.path.join(ordner, f"teil{i:02d}.mp4")
                _bild_zu_video(bild, t, SEKUNDEN_JE_BILD)
            teile.append(t)
        if not teile:
            raise Fehler("Karussell ohne Inhalte.")
        liste = os.path.join(ordner, "teile.txt")
        with open(liste, "w", encoding="utf-8") as f:
            f.writelines(f"file '{os.path.basename(t)}'\n" for t in teile)
        ziel = os.path.join(ordner, "video.mp4")
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", liste, "-c", "copy", "-movflags", "+faststart", ziel])
        for t in teile:
            os.remove(t)
        return ziel
    raise Fehler(f"Unbekannter Medientyp {typ}")


# ---------------------------------------------------------------- TikTok hochladen (Entwurf)

def _api(token, pfad, body):
    r = requests.post(f"{API}/{pfad}", json=body, timeout=60,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=UTF-8"})
    d = r.json()
    fehler = (d.get("error") or {})
    if fehler.get("code") not in (None, "ok"):
        raise Fehler(f"TikTok {fehler.get('code')}: {fehler.get('message')}")
    return d.get("data") or {}


def hochladen(token, datei):
    groesse = os.path.getsize(datei)
    mb = 1024 * 1024
    if groesse <= 64 * mb:
        chunk, anzahl = groesse, 1
    else:
        chunk = 10 * mb
        anzahl = groesse // chunk  # letzter Teil nimmt den Rest mit
    d = _api(token, "post/publish/inbox/video/init/", {
        "source_info": {"source": "FILE_UPLOAD", "video_size": groesse, "chunk_size": chunk, "total_chunk_count": anzahl}})
    with open(datei, "rb") as f:
        for i in range(anzahl):
            start = i * chunk
            ende = groesse - 1 if i == anzahl - 1 else start + chunk - 1
            f.seek(start)
            teil = f.read(ende - start + 1)
            r = requests.put(d["upload_url"], data=teil, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(teil)),
                "Content-Range": f"bytes {start}-{ende}/{groesse}"})
            if r.status_code not in (200, 201, 206):
                raise Fehler(f"Upload-Teil {i + 1} von {anzahl}: HTTP {r.status_code}")
    return d["publish_id"]


# ---------------------------------------------------------------- Fotoposts (1:1 wie Instagram)
# TikTok zieht Fotos nur von einer bestaetigten Adresse. Seit 09.11.2026: GitHub Pages im oeffentlichen Repo
# Spyr0701/tiktok-medien (URL prefix bei TikTok bestaetigt). Bilder liegen dort nur kurz und werden nach 2 Tagen geloescht.
MEDIEN_REPO = os.environ.get("TIKTOK_MEDIEN_REPO") or os.path.join(HIER, "medien_repo")
MEDIEN_URL = "https://spyr0701.github.io/tiktok-medien/"
MEDIEN_BEHALTEN_TAGE = 2


def fotos_bauen(m, ordner):
    """Liste von JPG-Pfaden fuer Bild, Bild-Story und reine Bild-Karussells; None, wenn ein Video dabei ist."""
    from PIL import Image
    typ = m.get("media_type")
    if typ == "IMAGE":
        quellen = [m["media_url"]]
    elif typ == "CAROUSEL_ALBUM":
        kinder = (m.get("children") or {}).get("data", [])
        if not kinder or any(c.get("media_type") != "IMAGE" for c in kinder):
            return None
        quellen = [c["media_url"] for c in kinder]
    else:
        return None
    os.makedirs(ordner, exist_ok=True)
    fotos = []
    for i, url in enumerate(quellen, 1):
        roh = os.path.join(ordner, f"roh{i:02d}")
        _laden(url, roh)
        ziel = os.path.join(ordner, f"{i:02d}.jpg")
        with Image.open(roh) as bild:
            bild = bild.convert("RGB")
            bild.thumbnail((1080, 1920))  # TikTok: hoechstens 1080p
            bild.save(ziel, "JPEG", quality=92)
        os.remove(roh)
        fotos.append(ziel)
    return fotos


def _git(*args):
    p = subprocess.run(["git", "-C", MEDIEN_REPO, *args], capture_output=True, text=True)
    if p.returncode:
        raise Fehler("git " + args[0] + ": " + (p.stderr or p.stdout).strip()[-300:])
    return p.stdout


def medien_aufraeumen():
    """Loescht Bildordner im Repo, die aelter als MEDIEN_BEHALTEN_TAGE sind (Ordnername beginnt mit JJJJ-MM-TT)."""
    if not os.path.isdir(MEDIEN_REPO):
        return
    grenze = (dt.date.today() - dt.timedelta(days=MEDIEN_BEHALTEN_TAGE)).isoformat()
    alt = [d for d in os.listdir(MEDIEN_REPO) if os.path.isdir(os.path.join(MEDIEN_REPO, d)) and d[:4].isdigit() and d[:10] < grenze]
    alt = [d for d in alt if _git("ls-files", d).strip()]  # nur was im Repo liegt, lokale Reste ignorieren
    if not alt:
        return
    _git("pull", "-q", "--rebase")
    for d in alt:
        _git("rm", "-r", "-q", d)
    _git("commit", "-q", "-m", f"Aufräumen: {len(alt)} Ordner")
    _git("push", "-q")
    log(f"Bildablage aufgeräumt: {len(alt)} alte Ordner gelöscht.")


def fotos_veroeffentlichen(fotos, name):
    """Legt die Bilder ins Pages-Repo, wartet bis sie abrufbar sind, gibt die URLs zurueck."""
    import shutil
    _git("pull", "-q", "--rebase")
    ziel = os.path.join(MEDIEN_REPO, name)
    os.makedirs(ziel, exist_ok=True)
    for f in fotos:
        shutil.copy2(f, ziel)
    _git("add", name)
    _git("commit", "-q", "-m", f"Bilder {name}")
    _git("push", "-q")
    urls = [MEDIEN_URL + name + "/" + os.path.basename(f) for f in fotos]
    for _ in range(36):  # GitHub Pages braucht meist unter einer Minute
        time.sleep(10)
        if all(requests.head(u, timeout=20).status_code == 200 for u in (urls[0], urls[-1])):
            return urls
    raise Fehler("Bilder nach 6 Minuten noch nicht auf GitHub Pages erreichbar.")


def foto_hochladen(token, urls, caption):
    erste = (caption.strip().split("\n")[0] if caption.strip() else "Ampullarium")[:90]
    d = _api(token, "post/publish/content/init/", {
        "post_info": {"title": erste, "description": caption[:4000]},
        "source_info": {"source": "PULL_FROM_URL", "photo_cover_index": 0, "photo_images": urls[:35]},
        "post_mode": "MEDIA_UPLOAD", "media_type": "PHOTO"})
    return d["publish_id"]


def status_holen(token, publish_id):
    return _api(token, "post/publish/status/fetch/", {"publish_id": publish_id}).get("status")


# ---------------------------------------------------------------- Ablauf

def _stand_laden():
    if os.path.exists(STAND):
        with open(STAND, encoding="utf-8") as f:
            return json.load(f)
    return {"start": None, "medien": {}}


def _stand_speichern(s):
    os.makedirs(DATEN, exist_ok=True)
    with open(STAND, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def lauf(probe=False):
    stand = _stand_laden()
    if not stand["start"]:
        # Erster Lauf: nur was ab jetzt auf Instagram erscheint, kein Nachladen alter Beitraege
        stand["start"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if not probe:
            _stand_speichern(stand)
        log(f"Erster Lauf, Startpunkt {stand['start']}. Ältere Beiträge werden nicht übertragen.")
    ab = dt.datetime.fromisoformat(stand["start"])
    if probe and "--ab" in sys.argv:
        ab = dt.datetime.fromisoformat(sys.argv[sys.argv.index("--ab") + 1]).replace(tzinfo=dt.timezone.utc)
    if not probe:
        try:
            medien_aufraeumen()
        except Exception as e:
            log(f"Aufräumen der Bildablage übersprungen: {e}")
    offen = [m for m in instagram_neu(ab) if stand["medien"].get(m["id"], {}).get("status") not in ("hochgeladen", "fertig")]
    if not offen:
        log("Nichts Neues auf Instagram.")
        return
    token = None if probe else _access_token()
    for m in offen[:MAX_JE_LAUF]:
        name = f"{m['_zeit'].astimezone():%Y-%m-%d_%H%M}_{m['art']}_{m['id']}"
        ordner = os.path.join(AUSGANG, name)
        eintrag = stand["medien"].setdefault(m["id"], {"art": m["art"], "zeit": m["timestamp"], "link": m.get("permalink")})
        try:
            os.makedirs(ordner, exist_ok=True)
            with open(os.path.join(ordner, "caption.txt"), "w", encoding="utf-8") as f:
                f.write(m.get("caption") or "")
            fotos = fotos_bauen(m, ordner)
            if fotos:  # Bild und Karussell 1:1 als TikTok-Fotopost, mit Caption
                if probe:
                    log(f"PROBE {m['art']} {m.get('media_type')} {m['timestamp']} → Fotopost mit {len(fotos)} Bildern, nicht hochgeladen")
                    continue
                urls = fotos_veroeffentlichen(fotos, name)
                eintrag["publish_id"] = foto_hochladen(token, urls, m.get("caption") or "")
                eintrag["status"] = "hochgeladen"
                eintrag["format"] = "fotos"
                eintrag["am"] = dt.datetime.now().isoformat(timespec="minutes")
                log(f"Fotopost-Entwurf an TikTok: {m['art']} vom {m['_zeit'].astimezone():%d.%m. %H:%M} ({len(fotos)} Bilder)")
                time.sleep(12)
                continue
            datei = datei_bauen(m, ordner)
            mb = os.path.getsize(datei) / 1e6
            if probe:
                log(f"PROBE {m['art']} {m.get('media_type')} {m['timestamp']} → {datei} ({mb:.1f} MB), nicht hochgeladen")
                continue
            eintrag["publish_id"] = hochladen(token, datei)
            eintrag["status"] = "hochgeladen"
            eintrag["am"] = dt.datetime.now().isoformat(timespec="minutes")
            log(f"Entwurf an TikTok: {m['art']} vom {m['_zeit'].astimezone():%d.%m. %H:%M} ({mb:.1f} MB)")
            time.sleep(12)  # TikTok: wenige Init-Aufrufe je Minute
        except Exception as e:  # einzelner Fehler stoppt nicht den Rest
            eintrag["status"] = "fehler"
            eintrag["fehler"] = str(e)[:300]
            log(f"FEHLER {m['art']} {m['id']}: {e}")
            if "too_many_pending" in str(e) or "rate_limit" in str(e):
                break
        finally:
            if not probe:
                _stand_speichern(stand)
    if len(offen) > MAX_JE_LAUF:
        log(f"{len(offen) - MAX_JE_LAUF} weitere warten auf den nächsten Lauf.")
    if not probe:
        for mid, e in stand["medien"].items():
            # Sandbox meldet dauerhaft PROCESSING_UPLOAD, obwohl der Entwurf ankommt (09.11.2026); nur 2 Tage nachfragen
            frisch = e.get("am", "") >= (dt.datetime.now() - dt.timedelta(days=2)).isoformat()
            if e.get("status") == "hochgeladen" and e.get("publish_id") and frisch:
                try:
                    s = status_holen(token, e["publish_id"])
                    e["tiktok_status"] = s
                    if s in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                        e["status"] = "fertig"
                    elif s == "FAILED":
                        e["status"] = "fehler"
                except Exception:
                    pass
        _stand_speichern(stand)


def status():
    s = _stand_laden()
    print(f"Startpunkt: {s['start']}")
    for mid, e in sorted(s["medien"].items(), key=lambda x: x[1]["zeit"])[-15:]:
        print(f"{e['zeit'][:16]}  {e['art']:<8} {e.get('status', '?'):<12} {e.get('tiktok_status', '')} {e.get('fehler', '')}")


if __name__ == "__main__":
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    befehl = sys.argv[1] if len(sys.argv) > 1 else "lauf"
    try:
        if befehl == "anmelden":
            anmelden()
        elif befehl == "anmelden-link":
            print(_auth_url())
        elif befehl == "anmelden-fertig":
            anmelden_fertig(sys.stdin.readline().strip())
        elif befehl == "probe":
            lauf(probe=True)
        elif befehl == "status":
            status()
        else:
            lauf()
    except (Fehler, instagram.IgFehler) as e:
        log(f"ABBRUCH: {e}")
        sys.exit(1)
