"""Pack PNG icons into a Windows .ico (PNG entries, Vista and later):  python3 make_ico.py out.ico icon-16.png icon-32.png ..."""
import struct
import sys

out, pngs = sys.argv[1], sys.argv[2:]
images = []
for path in pngs:
    data = open(path, "rb").read()
    w, h = struct.unpack(">II", data[16:24])  # PNG IHDR
    images.append((w, h, data))
header = struct.pack("<HHH", 0, 1, len(images))
offset = 6 + 16 * len(images)
entries, blobs = b"", b""
for w, h, data in images:
    entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
    blobs += data
open(out, "wb").write(header + entries + blobs)
print("wrote %s (%d sizes)" % (out, len(images)))
