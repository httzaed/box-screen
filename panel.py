"""
Trofeo Vision 9.16 LCD — pipeline complet
Résolution : 1920x462, JPEG, protocole LY USB bulk
"""

import io
import struct
import time

import usb.core
import usb.util
from PIL import Image, ImageDraw, ImageFont

# ── Constantes device ──────────────────────────────────────────────────────
VID = 0x0416
PID = 0x5408

WIDTH  = 1920
HEIGHT = 462

# ── Handshake ──────────────────────────────────────────────────────────────
_HANDSHAKE_PAYLOAD = bytes([
    0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]) + bytes(2032)

# ── Protocole LY frame ─────────────────────────────────────────────────────
_CHUNK_SIZE      = 512
_CHUNK_HEADER    = 16
_CHUNK_DATA      = 496   # 512 - 16
_USB_WRITE_SIZE  = 4096
_CMD_LY          = 1     # PID 0x5408


def _open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise RuntimeError("Panel introuvable — branche le câble USB")

    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    try:
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
    except usb.core.USBError:
        dev.reset()
        time.sleep(0.5)
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        usb.util.claim_interface(dev, intf.bInterfaceNumber)

    ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)

    ep_in = usb.util.find_descriptor(intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)

    return dev, intf, ep_out, ep_in


def _handshake(ep_out, ep_in):
    ep_out.write(_HANDSHAKE_PAYLOAD, timeout=2000)
    resp = bytes(ep_in.read(512, timeout=2000))
    if len(resp) < 9 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
        raise RuntimeError(f"Handshake invalide: {resp[:16].hex()}")
    return resp


def _image_to_jpeg(img: Image.Image, fit: bool = False) -> bytes:
    """Redimensionne, applique rotation 180°, encode en JPEG.

    fit=False (défaut) : étire à 1920x462
    fit=True           : garde le ratio, letterbox noir
    """
    img = img.convert("RGB")
    if fit:
        # Crop centré — scale pour remplir, coupe les bords
        src_ratio = img.width / img.height
        dst_ratio = WIDTH / HEIGHT
        if src_ratio > dst_ratio:
            # trop large — coupe les côtés
            new_h = HEIGHT
            new_w = int(HEIGHT * src_ratio)
        else:
            # trop haut — coupe haut/bas
            new_w = WIDTH
            new_h = int(WIDTH / src_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        x = (new_w - WIDTH)  // 2
        y = (new_h - HEIGHT) // 2
        img = img.crop((x, y, x + WIDTH, y + HEIGHT))
    else:
        img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    img = img.rotate(180)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _send_frame(ep_out, ep_in, jpeg_data: bytes):
    total_size = len(jpeg_data)
    num_chunks = total_size // _CHUNK_DATA + 1
    last_data  = total_size % _CHUNK_DATA

    chunks = bytearray(num_chunks * _CHUNK_SIZE)
    for i in range(num_chunks):
        off     = i * _CHUNK_SIZE
        is_last = (i == num_chunks - 1)
        dlen    = last_data if is_last else _CHUNK_DATA

        chunks[off]     = 0x01
        chunks[off + 1] = 0xFF
        struct.pack_into("<I", chunks, off + 2, total_size)
        struct.pack_into("<H", chunks, off + 6, dlen)
        chunks[off + 8] = _CMD_LY
        struct.pack_into("<H", chunks, off + 9,  num_chunks)
        struct.pack_into("<H", chunks, off + 11, i)

        src = i * _CHUNK_DATA
        chunks[off + _CHUNK_HEADER : off + _CHUNK_HEADER + dlen] = jpeg_data[src:src + dlen]

    # Pad à multiple de 4 chunks
    padded = num_chunks + (4 - num_chunks % 4) % 4
    send_buf = bytes(chunks) + bytes((padded - num_chunks) * _CHUNK_SIZE)
    total_bytes = padded * _CHUNK_SIZE

    pos = 0
    while pos < total_bytes:
        remaining = total_bytes - pos
        write_size = _USB_WRITE_SIZE if remaining >= _USB_WRITE_SIZE else min(2048, remaining)
        ep_out.write(send_buf[pos:pos + write_size], timeout=5000)
        pos += _USB_WRITE_SIZE

    ep_in.read(512, timeout=5000)  # ACK


# ── API publique ───────────────────────────────────────────────────────────

class Panel:
    def __init__(self):
        self._dev, self._intf, self._ep_out, self._ep_in = _open_device()
        _handshake(self._ep_out, self._ep_in)
        print(f"Panel connecté — {WIDTH}x{HEIGHT} JPEG")

    def send_image(self, img: Image.Image, fit: bool = False):
        jpeg = _image_to_jpeg(img, fit=fit)
        _send_frame(self._ep_out, self._ep_in, jpeg)

    def send_file(self, path: str):
        self.send_image(Image.open(path))

    def close(self):
        usb.util.release_interface(self._dev, self._intf.bInterfaceNumber)
        usb.util.dispose_resources(self._dev)


# ── Test rapide ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    panel = Panel()

    if len(sys.argv) > 1:
        print(f"Envoi de {sys.argv[1]}...")
        panel.send_file(sys.argv[1])
        print("OK")
    else:
        # Image de test : dégradé violet + texte
        img = Image.new("RGB", (WIDTH, HEIGHT), (20, 0, 40))
        draw = ImageDraw.Draw(img)
        for x in range(WIDTH):
            r = int(140 * x / WIDTH)
            g = int(82 * x / WIDTH)
            b = int(255 * x / WIDTH)
            draw.line([(x, 0), (x, HEIGHT)], fill=(r, g, b + 50))
        draw.text((60, HEIGHT // 2 - 20), "Trofeo Vision 9.16 — OK", fill=(255, 255, 255))
        panel.send_image(img)
        print("Image test envoyée")

    panel.close()
