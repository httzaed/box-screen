import serial.tools.list_ports

ports = serial.tools.list_ports.comports()
if not ports:
    print("Aucun port COM détecté")
else:
    for p in ports:
        print(p.device, "|", p.description, "|", "VID:", p.vid, "PID:", p.pid)
