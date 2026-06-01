import usb.core
import usb.util

VID = 0x0416
PID = 0x5408

dev = usb.core.find(idVendor=VID, idProduct=PID)
if dev is None:
    print("Device not found")
    exit(1)

print(f"Found: {dev}")
print(f"Manufacturer: {dev.manufacturer if dev.iManufacturer else '?'}")
print(f"Product: {dev.product if dev.iProduct else '?'}")

# Show all configurations and interfaces
for cfg in dev:
    print(f"\nConfig {cfg.bConfigurationValue}:")
    for intf in cfg:
        print(f"  Interface {intf.bInterfaceNumber} alt={intf.bAlternateSetting} class=0x{intf.bInterfaceClass:02X}")
        for ep in intf:
            direction = "OUT" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT else "IN"
            ep_type = {0: "CTRL", 2: "BULK", 3: "INTR"}.get(usb.util.endpoint_type(ep.bmAttributes), "?")
            print(f"    EP 0x{ep.bEndpointAddress:02X} {direction} {ep_type}")

# Try to claim interface 0
print("\nTrying to claim interface 0...")
try:
    usb.util.claim_interface(dev, 0)
    print("Claimed interface 0 OK")
    usb.util.release_interface(dev, 0)
except Exception as e:
    print(f"Failed: {e}")

usb.util.dispose_resources(dev)
