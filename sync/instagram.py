"""Instagram nur lesend: ein GET gegen die Graph API. Token nur im Header (Secret META_IG_TOKEN)."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASIS = "https://graph.facebook.com"


class IgFehler(Exception):
    pass


def _get(pfad, params=None):
    token = os.environ.get("META_IG_TOKEN", "").strip()
    if not token:
        raise IgFehler("META_IG_TOKEN fehlt.")
    url = f"{BASIS}/{pfad}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message")
        except Exception:
            msg = str(e)
        raise IgFehler(msg)
