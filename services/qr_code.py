"""A small QR Code encoder (ISO/IEC 18004) for the invoice seal: byte mode, error-correction level M, versions 1-6 (up to 106 bytes). Pure Python, no dependency.

`matrix(text)` -> list of rows of booleans (True = dark module), WITHOUT the quiet zone. The mask is chosen by the standard penalty rules. The tests decode the result with the
ZXing reader that already ships in the iPad app (webapp/static/zxing.min.js), so the encoder is checked against a real decoder, not against itself.
"""

# version -> (data codewords, EC codewords per block, blocks, alignment pattern centres), level M
_VERSIONS = {
    1: (16, 10, 1, ()),
    2: (28, 16, 1, (6, 18)),
    3: (44, 26, 1, (6, 22)),
    4: (64, 18, 2, (6, 26)),
    5: (86, 24, 2, (6, 30)),
    6: (108, 16, 4, (6, 34)),
}
_FORMAT_M = 0b00


class QRError(ValueError):
    pass


# ---------------------------------------------------------------- Reed-Solomon over GF(256), polynomial 0x11D
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coefficient in enumerate(poly):
            nxt[j] ^= coefficient
            nxt[j + 1] ^= _mul(coefficient, _EXP[i])
        poly = nxt
    return poly


def _remainder(data, degree):
    generator = _generator(degree)
    result = [0] * degree
    for byte in data:
        factor = byte ^ result[0]
        result = result[1:] + [0]
        for i in range(degree):
            result[i] ^= _mul(generator[i + 1], factor)
    return result


# ---------------------------------------------------------------- data
def _pick_version(length):
    for version, (data_cw, _ec, _blocks, _align) in _VERSIONS.items():
        if length <= data_cw - 2:                       # 4 mode bits + 8 count bits + data + terminator fit in data_cw bytes
            return version
    raise QRError("النص أطول من أن يُحوَّل إلى QR.")


def _codewords(payload, version):
    data_cw, ec_cw, blocks, _align = _VERSIONS[version]
    bits = "0100" + format(len(payload), "08b") + "".join(format(b, "08b") for b in payload)
    bits += "0" * min(4, data_cw * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    data = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(data) < data_cw:
        data.append(pad[(len(data) - len(bits) // 8) % 2])
    size = data_cw // blocks
    chunks = [data[i * size:(i + 1) * size] for i in range(blocks)]
    ecs = [_remainder(chunk, ec_cw) for chunk in chunks]
    out = []
    for i in range(size):
        out.extend(chunk[i] for chunk in chunks)
    for i in range(ec_cw):
        out.extend(ec[i] for ec in ecs)
    return out


# ---------------------------------------------------------------- matrix
def _format_bits(mask):
    data = (_FORMAT_M << 3) | mask
    value = data << 10
    for i in range(14, 9, -1):
        if value >> i & 1:
            value ^= 0x537 << (i - 10)
    return ((data << 10) | value) ^ 0x5412


def _build(version, codewords, mask):
    size = 17 + 4 * version
    dark = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def put(x, y, value, is_function=True):
        if 0 <= x < size and 0 <= y < size:
            dark[y][x] = value
            function[y][x] = is_function

    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):          # finder patterns with their separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                put(cx + dx, cy + dy, distance not in (2, 4))
    for i in range(8, size - 8):                                  # timing patterns
        put(i, 6, i % 2 == 0)
        put(6, i, i % 2 == 0)
    centres = _VERSIONS[version][3]
    for cy in centres:                                            # alignment patterns, except the three that would sit on a finder pattern
        for cx in centres:
            if (cx, cy) in ((centres[0], centres[0]), (centres[0], centres[-1]), (centres[-1], centres[0])):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
    put(8, size - 8, True)                                        # the always-dark module
    bits = _format_bits(mask)
    for i in range(15):                                           # format information, both copies
        bit = bool(bits >> i & 1)
        if i < 6:
            put(8, i, bit)
        elif i < 8:
            put(8, i + 1, bit)
        elif i == 8:
            put(7, 8, bit)
        else:
            put(14 - i, 8, bit)
        if i < 8:
            put(size - 1 - i, 8, bit)
        else:
            put(8, size - 15 + i, bit)
    # codewords: zig-zag from the bottom right, two columns at a time, skipping the timing column
    stream = "".join(format(c, "08b") for c in codewords)
    index, upward = 0, True
    x = size - 1
    while x > 0:
        if x == 6:
            x -= 1
        for step in range(size):
            y = size - 1 - step if upward else step
            for column in (x, x - 1):
                if function[y][column]:
                    continue
                bit = index < len(stream) and stream[index] == "1"
                index += 1
                if _mask(mask, column, y):
                    bit = not bit
                dark[y][column] = bit
        upward = not upward
        x -= 2
    return dark


def _mask(pattern, x, y):
    return ((y + x) % 2 == 0, y % 2 == 0, x % 3 == 0, (y + x) % 3 == 0, (y // 2 + x // 3) % 2 == 0,
            x * y % 2 + x * y % 3 == 0, (x * y % 2 + x * y % 3) % 2 == 0, ((y + x) % 2 + x * y % 3) % 2 == 0)[pattern]


def _penalty(dark):
    size, score = len(dark), 0
    for grid in (dark, [list(col) for col in zip(*dark)]):
        for row in grid:
            run = 1
            for i in range(1, size):
                if row[i] == row[i - 1]:
                    run += 1
                else:
                    score += run - 2 if run >= 5 else 0
                    run = 1
            score += run - 2 if run >= 5 else 0
    for y in range(size - 1):
        for x in range(size - 1):
            if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                score += 3
    pattern, reverse = [True, False, True, True, True, False, True, False, False, False, False], None
    reverse = pattern[::-1]
    for grid in (dark, [list(col) for col in zip(*dark)]):
        for row in grid:
            for i in range(size - 10):
                window = row[i:i + 11]
                if window == pattern or window == reverse:
                    score += 40
    total = sum(cell for row in dark for cell in row)
    score += abs(total * 20 - size * size * 10) // (size * size) * 10
    return score


def matrix(text):
    payload = str(text).encode("utf-8")
    version = _pick_version(len(payload))
    codewords = _codewords(payload, version)
    best = min((_penalty(grid), mask, grid) for mask in range(8) for grid in [_build(version, codewords, mask)])
    return [list(row) for row in best[2]]
