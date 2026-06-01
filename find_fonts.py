import os

FONT_DIRS = [
    r"C:\Windows\Fonts",
    os.path.expanduser(r"~\AppData\Local\Microsoft\Windows\Fonts"),
]

keywords = ["montserrat", "orbitron", "barlow"]

for d in FONT_DIRS:
    if not os.path.exists(d):
        continue
    for f in sorted(os.listdir(d)):
        if any(k in f.lower() for k in keywords):
            print(os.path.join(d, f))
