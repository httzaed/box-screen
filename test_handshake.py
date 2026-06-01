"""Test direct du protocole LY - handshake Trofeo Vision 9.16"""
import usb.core
import usb.util

VID = 0x0416
PID = 0x5408

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    print("Device not found")
    exit(1)

# Claim interface 0
cfg = dev.get_active_configuration()
intf = cfg[(0, 0)]
usb.util.claim_interface(dev, 0)

# Find endpoints
ep_out = usb.util.find_descriptor(intf, custom_match=lambda e:
    usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)

ep_in = usb.util.find_descriptor(intf, custom_match=lambda e:
    usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
    and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)

print(f"EP OUT: 0x{ep_out.bEndpointAddress:02X}")
print(f"EP IN:  0x{ep_in.bEndpointAddress:02X}")

# Handshake payload: 16-byte header + 2032 zeros
handshake = bytes([
    0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]) + bytes(2032)

print(f"\nSending handshake ({len(handshake)} bytes)...")
try:
    ep_out.write(handshake, timeout=5000)
    print("Handshake sent OK")
except Exception as e:
    print(f"Write failed: {e}")
    exit(1)

print("Reading response (512 bytes)...")
try:
    resp = bytes(ep_in.read(512, timeout=5000))
    print(f"Got {len(resp)} bytes")
    print(f"First 48 bytes: {' '.join(f'{b:02X}' for b in resp[:48])}")
    print(f"\nValidation:")
    print(f"  resp[0] == 0x03 ? {resp[0] == 3} (got 0x{resp[0]:02X})")
    print(f"  resp[1] == 0xFF ? {resp[1] == 0xFF} (got 0x{resp[1]:02X})")
    print(f"  resp[8] == 0x01 ? {resp[8] == 1} (got 0x{resp[8]:02X})")
    if resp[0] == 3 and resp[1] == 0xFF and resp[8] == 1:
        raw_pm = resp[20]
        if raw_pm <= 3:
            raw_pm = 1
        pm = 64 + raw_pm
        sub = resp[22] + 1 if len(resp) > 22 else 0
        print(f"\nHandshake OK! PM={pm}, SUB={sub}, resp[20]={resp[20]}")
    else:
        print("\nHandshake validation FAILED")
except Exception as e:
    print(f"Read failed: {e}")

usb.util.release_interface(dev, 0)
usb.util.dispose_resources(dev)
