import hid

devices = hid.enumerate()
if not devices:
    print("Aucun device HID détecté")
else:
    for d in devices:
        print(f"VID: {d['vendor_id']:04X}  PID: {d['product_id']:04X}  | {d['manufacturer_string']} — {d['product_string']}")
