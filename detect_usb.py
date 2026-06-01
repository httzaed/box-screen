import usb.core

devices = usb.core.find(find_all=True)
for d in devices:
    try:
        manufacturer = usb.util.get_string(d, d.iManufacturer) if d.iManufacturer else "?"
        product = usb.util.get_string(d, d.iProduct) if d.iProduct else "?"
    except Exception:
        manufacturer = "?"
        product = "?"
    print(f"VID: {d.idVendor:04X}  PID: {d.idProduct:04X}  | {manufacturer} — {product}")
