"""Animated GIF writing in pure Python.

Blender's Python has no imaging library (distribution builds lack even numpy),
so frames are rendered as 24-bit BMP, whose raw rows can be sliced at C speed,
mapped onto a fixed 216-color palette with 2x2 ordered dithering, and LZW
compressed here.
"""

import struct

# Red x green x blue palette levels: 216 colors. Equal levels keep grays neutral, because
# each pixel's channels then round the same way.
LEVELS = (6, 6, 6)
_BAYER = (0.125, 0.625, 0.875, 0.375)  # 2x2 thresholds for (even, odd) x (even, odd rows)


def palette():
    colors = bytearray()
    for r in range(LEVELS[0]):
        for g in range(LEVELS[1]):
            for b in range(LEVELS[2]):
                colors += bytes((round(r * 255 / (LEVELS[0] - 1)), round(g * 255 / (LEVELS[1] - 1)),
                                 round(b * 255 / (LEVELS[2] - 1))))
    return bytes(colors) + bytes(3 * 256 - len(colors))


def _channel_table(levels, threshold, weight):
    top = levels - 1
    return [min(top, int(value * top / 255.0 + threshold)) * weight for value in range(256)]


_TABLES = [(_channel_table(LEVELS[0], t, LEVELS[1] * LEVELS[2]), _channel_table(LEVELS[1], t, LEVELS[2]),
            _channel_table(LEVELS[2], t, 1)) for t in _BAYER]


def read_bmp(data):
    """Decode an uncompressed 24/32-bit BMP into (width, height, rows); rows are top-down BGR bytes."""
    if data[:2] != b"BM":
        raise ValueError("not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits, compression = struct.unpack_from("<HI", data, 28)
    if bits not in (24, 32) or compression not in (0, 3):
        raise ValueError("expected an uncompressed 24- or 32-bit BMP, got %d bits, compression %d"
                         % (bits, compression))
    channels = bits // 8
    stride = (width * channels + 3) & ~3
    rows = [data[offset + y * stride:offset + y * stride + width * channels] for y in range(abs(height))]
    if channels == 4:  # drop alpha: BGRA -> BGR
        rows = [bytes(b for i in range(0, len(row), 4) for b in row[i:i + 3]) for row in rows]
    if height > 0:  # stored bottom-up
        rows.reverse()
    return width, abs(height), rows


def quantize(rows):
    """Map top-down BGR rows to palette indices with ordered dithering."""
    out = bytearray()
    for y, row in enumerate(rows):
        line = bytearray(len(row) // 3)
        for parity in (0, 1):
            red, green, blue = _TABLES[(y & 1) * 2 + parity]
            start = parity * 3
            line[parity::2] = bytes(
                r + g + b for r, g, b in zip(map(red.__getitem__, row[start + 2::6]),
                                             map(green.__getitem__, row[start + 1::6]),
                                             map(blue.__getitem__, row[start::6])))
        out += line
    return bytes(out)


def _lzw(indices, min_code_size=8):
    """GIF-flavored LZW: variable code size up to 12 bits, LSB-first packing."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    out = bytearray()
    buffer = count = 0
    code_size, next_code = min_code_size + 1, end + 1
    table = {}

    def emit(code, size):
        nonlocal buffer, count
        buffer |= code << count
        count += size
        while count >= 8:
            out.append(buffer & 0xFF)
            buffer >>= 8
            count -= 8

    emit(clear, code_size)
    prefix = indices[0]
    for value in indices[1:]:
        key = (prefix << 8) | value
        code = table.get(key)
        if code is not None:
            prefix = code
            continue
        emit(prefix, code_size)
        if next_code < 4096:
            table[key] = next_code
            next_code += 1
            # Decoders add each entry one code later than we do, so widen after the
            # entry numbered 2**code_size exists, not when the table merely reaches it.
            if next_code > (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            emit(clear, code_size)
            table.clear()
            code_size, next_code = min_code_size + 1, end + 1
        prefix = value
    emit(prefix, code_size)
    emit(end, code_size)
    if count:
        out.append(buffer & 0xFF)
    return bytes(out)


def _blocks(data):
    chunks = [bytes((len(data[i:i + 255]),)) + data[i:i + 255] for i in range(0, len(data), 255)]
    return b"".join(chunks) + b"\x00"


class GifWriter:
    """Write frames of one size to an open binary file; loops forever."""

    def __init__(self, handle, width, height, delay_centiseconds):
        if not (0 < width < 65536 and 0 < height < 65536):
            raise ValueError("GIF frames must be 1-65535 pixels on each side")
        self.handle, self.width, self.height = handle, width, height
        self.delay = max(2, min(65535, int(round(delay_centiseconds))))
        self.frames = 0
        handle.write(b"GIF89a" + struct.pack("<HHBBB", width, height, 0xF7, 0, 0) + palette())
        handle.write(b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00")  # loop forever

    def add_frame(self, indices):
        if len(indices) != self.width * self.height:
            raise ValueError("frame has %d pixels, expected %d" % (len(indices), self.width * self.height))
        self.handle.write(b"\x21\xf9\x04\x04" + struct.pack("<H", self.delay) + b"\x00\x00")
        self.handle.write(b"\x2c" + struct.pack("<HHHHB", 0, 0, self.width, self.height, 0))
        self.handle.write(b"\x08" + _blocks(_lzw(indices)))
        self.frames += 1

    def add_bmp(self, data):
        width, height, rows = read_bmp(data)
        if (width, height) != (self.width, self.height):
            raise ValueError("frame is %dx%d, expected %dx%d" % (width, height, self.width, self.height))
        self.add_frame(quantize(rows))

    def close(self):
        self.handle.write(b"\x3b")
