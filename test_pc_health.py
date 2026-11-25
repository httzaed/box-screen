"""Tests des fonctions pures de pc_health.py (python3 test_pc_health.py)."""
import pc_health as ph


def test_parse_smi():
    g = ph.parse_smi("NVIDIA GeForce RTX 3070, 87, 71, 6123, 8192, 182.35, 220.00, 1905, 64")
    assert g["name"].startswith("NVIDIA") and g["util"] == 87 and g["temp"] == 71
    assert g["vram_used_mb"] == 6123 and g["power_w"] == 182.35 and g["fan_pct"] == 64
    g = ph.parse_smi("GPU, 12, 40, 500, 4096, [N/A], [N/A], 300, [N/A]")
    assert g["power_w"] is None and g["fan_pct"] is None and g["clock_mhz"] == 300
    assert ph.parse_smi("") is None


def test_parse_lhm():
    data = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [{"Text": "AMD Ryzen 7", "Children": [
        {"Text": "Temperatures", "Children": [{"Text": "Core (Tctl/Tdie)", "Value": "68,5 °C"}]},
        {"Text": "Load", "Children": [{"Text": "CPU Total", "Value": "40 %"}]}]}]}]}
    assert ph.parse_lhm_cpu_temp(data) == 68.5
    assert ph.parse_lhm_cpu_temp({"Text": "x"}) is None


def test_top_processes():
    rows = [("chrome.exe", 3.0, 100 * 1048576), ("chrome.exe", 2.0, 50 * 1048576),
            ("VALORANT-Win64-Shipping.exe", 22.5, 3000 * 1048576), ("python.exe", 1.0, 80 * 1048576)]
    top = ph.top_processes(rows, 2)
    assert [t["name"] for t in top] == ["VALORANT-Win64-Shipping", "chrome"]
    assert top[1]["cpu"] == 5.0 and top[1]["ram_mb"] == 150 and top[1]["count"] == 2


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("OK", n)
