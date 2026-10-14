"""
Alien AI helpers for suite nodes: Alien's settings (key, models), OpenRouter chat (text, and images for vision) and
image generation, with caches in <repository>/.alien/cache. Keep this file next to the nodes that import it.

The key is read through the Alien plugin's settings (settings.json "openrouterKey"); it is never written anywhere.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
UA = {"User-Agent": "Alien/2.0 (Grasshopper suite node)"}


def setting(key):
    try:
        import clr
        clr.AddReference("Alien")
        from Alien import ScriptRepository
        return ScriptRepository.Setting(key)
    except Exception:
        base = os.environ.get("APPDATA") or os.path.expanduser("~/Library/Application Support")
        try:
            with open(os.path.join(base, "Alien", "settings.json")) as f:
                return json.load(f).get(key)
        except (OSError, ValueError):
            return None


def repository():
    """The script repository (Alien's setting), else the folder holding this file."""
    try:
        import clr
        clr.AddReference("Alien")
        from Alien import ScriptRepository
        if ScriptRepository.Root:
            return str(ScriptRepository.Root)
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def cache_dir(kind):
    path = os.path.join(repository(), ".alien", "cache", kind)
    os.makedirs(path, exist_ok=True)
    return path


def key_of(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _post(body, timeout=180):
    api_key = (setting("openrouterKey") or "").strip()
    if not api_key:
        raise ValueError("No OpenRouter key yet: open the Alien panel's Help (?) and follow \"Connect OpenRouter\".")
    body = dict(body, usage={"include": True}, provider={"data_collection": "deny"})
    req = urllib.request.Request(OPENROUTER, data=json.dumps(body).encode(),
                                 headers=dict(UA, Authorization="Bearer " + api_key, **{"Content-Type": "application/json", "X-Title": "Alien"}))
    last = None
    for attempt in range(2):  # one resend for a transient failure
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.loads(r.read())
            if "error" in res:
                raise RuntimeError("%s: %s" % (body.get("model"), res["error"].get("message")))
            return res
        except urllib.error.HTTPError as ex:
            last = RuntimeError("%s: HTTP %d %s" % (body.get("model"), ex.code, ex.read()[:300].decode("utf-8", "replace")))
            if ex.code < 500 and ex.code != 429:
                break
        except (urllib.error.URLError, TimeoutError) as ex:
            last = RuntimeError("%s: %s" % (body.get("model"), ex))
        time.sleep(1.5)
    raise last


# ── text and vision ──

def image_part(data, mime="image/png"):
    return {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode())}}


def load_image(src):
    """A reference image from a file path or an http(s) URL -> (bytes, mime)."""
    src = (src or "").strip().strip('"')
    if src.lower().startswith(("http://", "https://")):
        path = os.path.join(cache_dir("downloads"), key_of(src))  # fetched once, not on every solve
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            return data, sniff_mime(data) or "image/png"
        with urllib.request.urlopen(urllib.request.Request(src, headers=UA), timeout=30) as r:
            data, mime = r.read(), r.headers.get_content_type()
        with open(path, "wb") as f:
            f.write(data)
    else:
        with open(os.path.expanduser(src), "rb") as f:
            data = f.read()
        mime = None
    return data, sniff_mime(data) or mime or "image/png"


def sniff_mime(data):
    """The image type from its first bytes (file extensions and servers are often wrong)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def chat(messages, model, max_tokens=6000, temperature=0.2, reasoning="low"):
    """-> (reply text, US$ cost). messages: [{"role", "content"}] where content may hold image parts."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    res = _post(body)
    text = res["choices"][0]["message"].get("content") or ""
    return text, res.get("usage", {}).get("cost") or 0.0


# ── image generation (cached) ──

DEFAULT_IMAGE_MODEL = "google/gemini-3.1-flash-lite-image"


def image(prompt, aspect="1:1", model=None, allow_new=True):
    """
    -> (image bytes, meta). Cached by (model, prompt, aspect) in <repository>/.alien/cache/images. With
    allow_new=False only the cache is read and None means it hasn't been made (a node's Generate button makes it).
    """
    model = model or setting("imageModel") or DEFAULT_IMAGE_MODEL
    path = os.path.join(cache_dir("images"), key_of(model, prompt, aspect))
    if os.path.exists(path + ".img"):
        with open(path + ".img", "rb") as f:
            data = f.read()
        with open(path + ".json") as f:
            meta = json.load(f)
        meta.update(cached=True, path=path + ".img")
        return data, meta
    if not allow_new:
        return None
    t = time.time()
    body = {"model": model, "modalities": ["image", "text"], "messages": [{"role": "user", "content": prompt}]}
    if model.startswith("google/"):
        body["image_config"] = {"aspect_ratio": aspect}
    res = _post(body, timeout=120)
    msg = res["choices"][0]["message"]
    images = msg.get("images") or []
    if not images:
        raise RuntimeError("%s drew nothing (said: %s)" % (model, (msg.get("content") or "")[:150]))
    data = base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])
    meta = {"model": model, "prompt": prompt, "aspect": aspect, "cost": res.get("usage", {}).get("cost") or 0.0,
            "seconds": round(time.time() - t, 1), "created": time.strftime("%Y-%m-%d %H:%M")}
    with open(path + ".img", "wb") as f:
        f.write(data)
    with open(path + ".json", "w") as f:
        json.dump(meta, f)
    meta.update(cached=False, path=path + ".img")
    return data, meta
