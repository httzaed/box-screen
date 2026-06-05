#!/usr/bin/env python3
"""
Détection des ventilateurs disponibles sur ce système.
Essaie : nvidia-smi, LibreHardwareMonitor (HTTP), WMI, psutil.
"""
import subprocess
import json
import sys

# ── 1. nvidia-smi (GPU fan) ─────────────────────────────────────────────────
print("=" * 50)
print("1. nvidia-smi")
try:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=index,name,fan.speed",
         "--format=csv,noheader,nounits"], text=True
    ).strip()
    for line in out.splitlines():
        idx, name, fan = [x.strip() for x in line.split(",")]
        print(f"  GPU {idx} ({name}) — fan: {fan}%")
except Exception as e:
    print(f"  Erreur: {e}")

# ── 2. LibreHardwareMonitor HTTP API ─────────────────────────────────────────
print("\n2. LibreHardwareMonitor (http://localhost:8085)")
try:
    import urllib.request
    with urllib.request.urlopen("http://localhost:8085/data.json", timeout=2) as r:
        data = json.loads(r.read())

    def walk(node, depth=0):
        name = node.get("Text", "")
        val  = node.get("Value", "")
        if "Fan" in name or "RPM" in name or "fan" in name.lower():
            print(f"  {'  '*depth}{name}: {val}")
        for child in node.get("Children", []):
            walk(child, depth + 1)

    walk(data)
except Exception as e:
    print(f"  Erreur: {e}")
    print("  → Lance LibreHardwareMonitor avec 'Remote Web Server' activé (port 8085)")

# ── 3. WMI ───────────────────────────────────────────────────────────────────
print("\n3. WMI (Win32_Fan / Win32_CoolingDevice)")
try:
    import wmi
    c = wmi.WMI()
    found = False
    for obj in c.Win32_Fan():
        print(f"  Fan: {obj.Name}  ActiveCooling={obj.ActiveCooling}")
        found = True
    for obj in c.Win32_CoolingDevice():
        print(f"  CoolingDevice: {obj.Name}")
        found = True
    if not found:
        print("  Aucun ventilateur exposé via WMI")
except Exception as e:
    print(f"  Erreur: {e}")

# ── 4. psutil ────────────────────────────────────────────────────────────────
print("\n4. psutil.sensors_fans()")
try:
    import psutil
    fans = psutil.sensors_fans()
    if fans:
        for name, entries in fans.items():
            for e in entries:
                print(f"  {name} / {e.label}: {e.current} RPM")
    else:
        print("  Aucun ventilateur détecté (normal sous Windows)")
except Exception as e:
    print(f"  Erreur: {e}")

print("\n" + "=" * 50)
print("Résumé: pour afficher les RPM dans le panel,")
print("LibreHardwareMonitor (HTTP API) est la méthode la plus complète.")
print("Télécharge-le sur https://github.com/LibreHardwareMonitor/LibreHardwareMonitor")
print("et active Options > Remote Web Server.")
