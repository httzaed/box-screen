"""Santé du PC pour le dashboard : CPU, RAM, GPU, disques, réseau, processus.

Échantillonnage en tâche de fond toutes les secondes, démarré au premier appel de snapshot().

    snapshot()  dict prêt à servir sur GET /api/pc

Sources :
    psutil                      CPU (total et par cœur), fréquence, RAM, swap, disques, réseau, processus
    nvidia-smi                  GPU NVIDIA : charge, température, VRAM, puissance, fréquence, ventilateur
    LibreHardwareMonitor        (optionnel) température CPU, si son serveur web est activé
                                (Options > Remote Web Server > Run, port 8085)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from collections import deque

try:
    import psutil
except ImportError:          # psutil est déjà utilisé par render.py, mais on reste robuste
    psutil = None

HIST = 90                    # secondes d'historique pour les courbes
GAME_EXE = "valorant-win64-shipping.exe"
LHM_URL = "http://127.0.0.1:8085/data.json"

_lock = threading.Lock()
_started = False
_hist = {"cpu": deque(maxlen=HIST), "gpu": deque(maxlen=HIST), "ram": deque(maxlen=HIST)}
_state: dict = {"ok": False, "error": None, "updated": 0.0}
_proc_cache: dict = {}


def _find_smi() -> str | None:
    p = shutil.which("nvidia-smi")
    if p:
        return p
    for c in (r"C:\Windows\System32\nvidia-smi.exe", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.exists(c):
            return c
    return None


_SMI = _find_smi()


# ── Parsing (fonctions pures, testées dans test_pc_health.py) ───────────────

def _num(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_smi(line: str) -> dict | None:
    """Une ligne CSV de nvidia-smi (voir _gpu) → dict. '[N/A]' devient None."""
    if not line or not line.strip():
        return None
    parts = [p.strip() for p in line.split(",")]
    parts += [None] * (9 - len(parts))
    name, util, temp, mu, mt, pw, pl, clk, fan = parts[:9]
    return {"name": name or "GPU", "util": _num(util), "temp": _num(temp),
            "vram_used_mb": _num(mu), "vram_total_mb": _num(mt),
            "power_w": _num(pw), "power_limit_w": _num(pl),
            "clock_mhz": _num(clk), "fan_pct": _num(fan)}


def parse_lhm_cpu_temp(data) -> float | None:
    """JSON de LibreHardwareMonitor → température CPU (« CPU Package » ou « Core (Tctl/Tdie) »)."""
    best = None

    def walk(node, in_temps=False):
        nonlocal best
        if not isinstance(node, dict):
            return
        text = str(node.get("Text") or "")
        temps = in_temps or text.lower() == "temperatures"
        if temps and any(k in text for k in ("CPU Package", "Tctl/Tdie", "Core (Tctl")):
            v = str(node.get("Value") or "").replace(",", ".").replace("°C", "").strip()
            try:
                best = float(v)
            except ValueError:
                pass
        for ch in node.get("Children") or []:
            walk(ch, temps)

    walk(data)
    return best


def top_processes(rows: list[tuple[str, float, int]], n: int = 5) -> list[dict]:
    """[(nom, cpu %, ram octets)] → regroupé par nom (Chrome = un seul), trié par CPU puis RAM."""
    agg: dict = {}
    for name, cpu, mem in rows:
        key = (name or "?").lower()
        a = agg.setdefault(key, {"name": name or "?", "cpu": 0.0, "ram_mb": 0.0, "count": 0})
        a["cpu"] += cpu or 0.0
        a["ram_mb"] += (mem or 0) / 1048576
        a["count"] += 1
    out = sorted(agg.values(), key=lambda a: (-a["cpu"], -a["ram_mb"]))[:n]
    for a in out:
        a["cpu"] = round(a["cpu"], 1)
        a["ram_mb"] = round(a["ram_mb"])
        a["name"] = a["name"][:-4] if a["name"].lower().endswith(".exe") else a["name"]
    return out


# ── Mesures ─────────────────────────────────────────────────────────────────

def _gpu() -> dict | None:
    if not _SMI:
        return None
    try:
        out = subprocess.run(
            [_SMI, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,"
                   "power.draw,power.limit,clocks.gr,fan.speed", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = out.stdout.strip().splitlines()
        return parse_smi(lines[0]) if lines else None
    except Exception:
        return None


def _cpu_temp() -> float | None:
    try:
        with urllib.request.urlopen(LHM_URL, timeout=0.8) as r:
            return parse_lhm_cpu_temp(json.loads(r.read().decode("utf-8", "replace")))
    except Exception:
        return None


def _procs() -> tuple[list[dict], dict | None]:
    n = psutil.cpu_count() or 1
    rows, seen, game = [], set(), None
    for p in psutil.process_iter(["name"]):
        pid = p.pid
        if pid == 0:
            continue
        seen.add(pid)
        cached = _proc_cache.get(pid)
        if cached is None:
            _proc_cache[pid] = p
            try:
                p.cpu_percent(None)          # premier relevé : la mesure arrive au tour suivant
            except Exception:
                pass
            continue
        try:
            cpu = cached.cpu_percent(None) / n
            mem = cached.memory_info().rss
        except Exception:
            continue
        name = (cached.info or {}).get("name") or ""
        if name.lower() in ("system idle process", "idle", "memory compression"):
            continue
        rows.append((name, cpu, mem))
        if name.lower() == GAME_EXE:
            game = {"cpu": round(cpu, 1), "ram_mb": round(mem / 1048576)}
    for pid in list(_proc_cache):
        if pid not in seen:
            _proc_cache.pop(pid, None)
    return top_processes(rows), game


def _disks() -> list[dict]:
    out = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        out.append({"mount": part.mountpoint.rstrip("\\/") or part.mountpoint, "pct": round(u.percent),
                    "used_gb": round(u.used / 1e9), "total_gb": round(u.total / 1e9)})
    return out[:4]


def _loop() -> None:
    psutil.cpu_percent(None)
    psutil.cpu_percent(None, percpu=True)
    last_net = last_disk = None
    gpu, procs, game, disks, cpu_temp = None, [], None, [], None
    tick = 0
    while True:
        t0 = time.time()
        try:
            cpu = psutil.cpu_percent(None)
            cores = psutil.cpu_percent(None, percpu=True)
            freq = psutil.cpu_freq()
            vm, sw = psutil.virtual_memory(), psutil.swap_memory()
            net, dio = psutil.net_io_counters(), psutil.disk_io_counters()
            net_rate = disk_rate = None
            if last_net:
                dt = max(0.2, t0 - last_net[0])
                net_rate = {"down_bps": (net.bytes_recv - last_net[1].bytes_recv) / dt,
                            "up_bps": (net.bytes_sent - last_net[1].bytes_sent) / dt}
            if last_disk and dio:
                dt = max(0.2, t0 - last_disk[0])
                disk_rate = {"read_bps": (dio.read_bytes - last_disk[1].read_bytes) / dt,
                             "write_bps": (dio.write_bytes - last_disk[1].write_bytes) / dt}
            last_net, last_disk = (t0, net), ((t0, dio) if dio else None)
            if tick % 2 == 0:
                gpu = _gpu()
            if tick % 3 == 0:
                procs, game = _procs()
            if tick % 5 == 0:
                cpu_temp = _cpu_temp()
            if tick % 30 == 0:
                disks = _disks()
            with _lock:
                _hist["cpu"].append(round(cpu, 1))
                _hist["ram"].append(round(vm.percent, 1))
                _hist["gpu"].append(gpu["util"] if gpu and gpu.get("util") is not None else None)
                _state.update({
                    "ok": True, "error": None, "updated": t0,
                    "cpu": {"pct": round(cpu, 1), "cores": [round(c) for c in cores],
                            "freq_mhz": round(freq.current) if freq else None,
                            "count": psutil.cpu_count(), "temp": cpu_temp},
                    "ram": {"pct": round(vm.percent, 1), "used_gb": round(vm.used / 1073741824, 1),
                            "total_gb": round(vm.total / 1073741824, 1)},
                    "swap": {"pct": round(sw.percent, 1)},
                    "gpu": gpu, "disks": disks, "net": net_rate, "disk_io": disk_rate,
                    "procs": procs, "game": game,
                    "uptime_s": round(t0 - psutil.boot_time()),
                })
        except Exception as e:
            with _lock:
                _state.update({"ok": False, "error": str(e)[:200]})
        tick += 1
        time.sleep(max(0.1, 1.0 - (time.time() - t0)))


def snapshot() -> dict:
    global _started
    if psutil is None:
        return {"ok": False, "error": "psutil absent : python3 -m pip install psutil"}
    if not _started:
        _started = True
        threading.Thread(target=_loop, name="pc-health", daemon=True).start()
    with _lock:
        out = dict(_state)
        out["hist"] = {k: list(v) for k, v in _hist.items()}
        out["gpu_source"] = "nvidia-smi" if _SMI else None
    return out
