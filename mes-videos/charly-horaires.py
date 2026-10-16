#!/usr/bin/env python3
"""Horaires de l'enfant, réglés par les parents depuis la page admin de Charly Guard.

Lancé par root toutes les minutes (charly-horaires.timer) :
- relit les horaires sur le serveur (GET /schedule) toutes les 2 minutes ; sans réseau, garde les derniers connus
  (et 8h-20h si le serveur n'a jamais répondu) ;
- réécrit la règle pam_time (/etc/security/time.conf) : en dehors des plages, on ne peut pas se connecter ;
- prévient l'enfant quelques minutes avant la fin d'une plage, puis ferme sa session à la fin.

Usage : charly-horaires.py <compte-enfant>
Réglages : /etc/studio-dj-kids/guard.conf (CG_BASE, CG_AGENT_TOKEN)
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CHILD = sys.argv[1] if len(sys.argv) > 1 else "djpiloupilou"
CONF = Path("/etc/studio-dj-kids/guard.conf")
STATE = Path("/var/lib/charly-horaires")
CACHE = STATE / "horaires.json"
WARNED = STATE / "prevenu.json"
TIME_CONF = Path("/etc/security/time.conf")
MARK = "# STUDIO-DJ-KIDS couvre-feu"
FETCH_EVERY = 110  # secondes (le timer passe toutes les minutes)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PAM_DAYS = {"mon": "Mo", "tue": "Tu", "wed": "We", "thu": "Th", "fri": "Fr", "sat": "Sa", "sun": "Su"}
DEFAULT = {"timezone": "Europe/Paris", "warn_min": 10, "days": {d: ["08:00-20:00"] for d in DAYS}, "overrides": {}}


def read_conf():
    conf = {}
    for line in CONF.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()
    return conf["CG_BASE"].rstrip("/"), conf["CG_AGENT_TOKEN"]


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(path, data):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)


def valid(schedule):
    return isinstance(schedule, dict) and isinstance(schedule.get("days"), dict)


def schedule():
    cached = load(CACHE, None)
    fresh = cached and time.time() - cached.get("_fetched", 0) < FETCH_EVERY
    if not fresh:
        try:
            base, token = read_conf()
            req = urllib.request.Request(base + "/schedule", headers={"Authorization": f"Bearer {token}"})
            data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if valid(data):
                data["_fetched"] = time.time()
                save(CACHE, data)
                cached = data
        except Exception as e:  # pas de réseau : on garde les derniers horaires connus
            print(f"horaires pas relus ({e})", flush=True)
    return cached if valid(cached) else DEFAULT


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def parse(ranges):
    out = []
    for r in ranges or []:
        try:
            start, end = (minutes(x.strip()) for x in r.split("-"))
        except ValueError:
            continue
        if 0 <= start < end <= 24 * 60:
            out.append((start, end))
    return sorted(out)


def ranges_for(sched, day):
    """Plages (en minutes depuis minuit) pour cette date, exception du jour comprise."""
    ranges = parse(sched["days"].get(DAYS[day.weekday()], []))
    override = (sched.get("overrides") or {}).get(day.isoformat()) or {}
    if override.get("off"):
        return []
    if "ranges" in override:
        ranges = parse(override["ranges"])
    extra = int(override.get("extra_min") or 0)
    if extra and ranges:  # le temps en plus prolonge la dernière plage de la journée
        start, end = ranges[-1]
        ranges[-1] = (start, min(24 * 60, end + extra))
    return ranges


def pam_rule(sched, today):
    """Règle pam_time : la semaine type, sauf aujourd'hui qui suit l'exception éventuelle."""
    parts = []
    for i, d in enumerate(DAYS):
        day = today + dt.timedelta(days=(i - today.weekday()) % 7)
        for start, end in ranges_for(sched, day) if day == today else parse(sched["days"].get(d, [])):
            parts.append(f"{PAM_DAYS[d]}{start // 60:02d}{start % 60:02d}-{end // 60:02d}{end % 60:02d}")
    return f"*;*;{CHILD};{'|'.join(parts) if parts else '!Al0000-2400'}"


def write_pam(rule):
    lines = TIME_CONF.read_text().splitlines()
    if MARK in lines:
        i = lines.index(MARK)
        if i + 1 < len(lines) and lines[i + 1] == rule:
            return
        if i + 1 < len(lines) and lines[i + 1].startswith(f"*;*;{CHILD};"):
            lines[i + 1] = rule
        else:
            lines.insert(i + 1, rule)
    else:
        lines += [MARK, rule]
    tmp = TIME_CONF.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    tmp.replace(TIME_CONF)
    print(f"règle pam_time : {rule}", flush=True)


def child_logged_in():
    return subprocess.run(["loginctl", "show-user", CHILD], capture_output=True).returncode == 0


def notify(title, body):
    uid = subprocess.run(["id", "-u", CHILD], capture_output=True, text=True).stdout.strip()
    subprocess.run(["runuser", "-u", CHILD, "--", "env", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                    "gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                    "--object-path", "/org/freedesktop/Notifications",
                    "--method", "org.freedesktop.Notifications.Notify",
                    "Horaires", "0", "alarm-symbolic", title, body, "[]", "{'urgency': <byte 2>}", "-1"],
                   capture_output=True)


def main():
    sched = schedule()
    now = dt.datetime.now()
    today = now.date()
    write_pam(pam_rule(sched, today))
    if not child_logged_in():
        return
    t = now.hour * 60 + now.minute
    current = next(((s, e) for s, e in ranges_for(sched, today) if s <= t < e), None)
    if current is None:
        notify("C'est l'heure !", "L'ordinateur s'arrête pour toi maintenant. À tout à l'heure 🎧")
        time.sleep(20)
        subprocess.run(["loginctl", "terminate-user", CHILD])
        print("hors des horaires : session fermée", flush=True)
        return
    left = current[1] - t
    warn = int(sched.get("warn_min") or 10)
    key = f"{today.isoformat()} {current[1]}"
    warned = load(WARNED, {})
    if left <= warn and warned.get("big") != key:
        notify(f"Plus que {left} minutes !", f"L'ordinateur s'arrête pour toi à {current[1] // 60}h{current[1] % 60:02d}. "
               "Pense à sauvegarder ton travail 🎧")
        warned["big"] = key
    if left <= 1 and warned.get("last") != key:
        notify("Plus qu'une minute !", "Sauvegarde vite ton travail 💾")
        warned["last"] = key
    save(WARNED, warned)


if __name__ == "__main__":
    main()
