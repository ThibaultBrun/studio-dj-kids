#!/usr/bin/env python3
"""Historique léger des sites visités par l'enfant : les noms de domaine et l'heure, rien d'autre.

Lancé par root (service charly-dns-log). Écoute les requêtes DNS du PC avec `resolvectl monitor`,
garde seulement celles faites pendant que la session de l'enfant est active (pas celles des parents),
et les envoie par lots au serveur Charly Guard (POST /log). Sans Internet, les lots attendent sur le disque.

Usage : charly-dns-log.py <compte-enfant>
Réglages : /etc/studio-dj-kids/guard.conf (CG_BASE, CG_AGENT_TOKEN)
"""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

CHILD = sys.argv[1] if len(sys.argv) > 1 else "djpiloupilou"
CONF = Path("/etc/studio-dj-kids/guard.conf")
QUEUE = Path("/var/lib/charly-dns-log/file.json")
SEND_EVERY = 60  # secondes
SAME_DOMAIN_GAP = 300  # un même domaine n'est noté qu'une fois toutes les 5 minutes
MAX_QUEUE = 20000
# Bruit technique qui n'apprend rien aux parents
IGNORE_SUFFIXES = (".local", ".arpa", ".lan", ".internal", "ubuntu.com", "snapcraft.io", "kde.org",
                   "mozilla.net", "mozilla.com", "mozilla.org", "firefox.com", "gstatic.com", "googleapis.com")

lock = threading.Lock()
pending = []
last_seen = {}


def read_conf():
    conf = {}
    for line in CONF.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf["CG_BASE"].rstrip("/"), conf["CG_AGENT_TOKEN"]


def child_active():
    """Vrai si l'enfant a une session ouverte au premier plan (pas verrouillée derrière une autre)."""
    out = subprocess.run(["loginctl", "show-user", CHILD, "-p", "State", "--value"],
                         capture_output=True, text=True).stdout.strip()
    return out == "active"


def domains_in(event):
    """Noms demandés et « bloqué ou pas » dans une ligne JSON de resolvectl monitor."""
    names = set()
    for q in event.get("question") or []:
        name = (q.get("name") or "").rstrip(".").lower()
        if name:
            names.add(name)
    if not names:
        key = event.get("key") or {}
        name = (key.get("name") or "").rstrip(".").lower()
        if name:
            names.add(name)
    answers = json.dumps(event.get("answer") or [])
    if ".rpz." in answers:  # CleanBrowsing répond « politique d'accès » quand il bloque un site
        return names, "blocked"
    if event.get("state") != "success":
        return set(), ""  # nom qui n'existe pas, panne réseau : rien à noter
    return names, "allowed"


def record(name, action):
    if name.endswith(IGNORE_SUFFIXES) or "." not in name:
        return
    now = time.time()
    if now - last_seen.get(name, 0) < SAME_DOMAIN_GAP:
        return
    last_seen[name] = now
    with lock:
        pending.append({"ts": int(now * 1000), "host": name, "url": "", "method": "DNS", "action": action})


def load_queue():
    try:
        return json.loads(QUEUE.read_text())
    except (OSError, ValueError):
        return []


def save_queue(events):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE.with_suffix(".tmp")
    tmp.write_text(json.dumps(events[-MAX_QUEUE:]))
    tmp.replace(QUEUE)


def sender():
    base, token = read_conf()
    while True:
        time.sleep(SEND_EVERY)
        with lock:
            events = load_queue() + pending
            pending.clear()
        if not events:
            continue
        try:
            req = urllib.request.Request(base + "/log", data=json.dumps({"events": events[:2000]}).encode(),
                                         headers={"Authorization": f"Bearer {token}",
                                                  "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20).read()
            events = events[2000:]
        except Exception as e:  # pas de réseau ou serveur en panne : on réessaiera
            print(f"envoi impossible ({e}), {len(events)} en attente", flush=True)
        save_queue(events)


def main():
    threading.Thread(target=sender, daemon=True).start()
    active, checked = False, 0
    while True:
        proc = subprocess.Popen(["resolvectl", "monitor", "--json=short"], stdout=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if time.time() - checked > 15:
                active, checked = child_active(), time.time()
            if not active:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            names, action = domains_in(event)
            for name in names:
                record(name, action)
        proc.wait()
        time.sleep(5)  # resolved redémarré : on se rebranche


if __name__ == "__main__":
    main()
