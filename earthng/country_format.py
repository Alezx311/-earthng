"""Binary source cells of native country packages (earthng-features-v2).

A region stores its 1 km source cells in blocks of BLOCK x BLOCK cells, one file per block:

    block   := 'EGB2' varint(count) count * (varint(slot) varint(length)) payloads
    slot    := (i - bi*BLOCK) + (j - bj*BLOCK) * BLOCK, ascending
    payload := strings terrain roads junctions buildings areas lines points

All numbers are unsigned LEB128 varints; signed ones are zigzagged. Lengths, heights and
coordinates are integer centimetres, terrain heights decimetres. Every polyline or ring starts at the cell origin
(i*cellSize, j*cellSize, 0) and continues as deltas, so equal source points round to equal
integers in every cell and neighbouring tiles keep identical seams. The ZIP's deflate does the
general-purpose compression; the game reads these files with plain string.byte.
"""
import math

MAGIC = b'EGB2'
BLOCK = 8
SCALE = 100
TERRAIN = 10       # DEM heights: decimetres (the z12 source is metre-scale anyway)
KINDS = ('roads', 'junctions', 'buildings', 'areas', 'lines', 'points')


def _cm(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('non-finite source number')
    return int(round(value * SCALE))


class _Writer:
    def __init__(self):
        self.out = bytearray()

    def u(self, value):
        if value < 0:
            raise ValueError('negative unsigned varint')
        while value >= 0x80:
            self.out.append((value & 0x7f) | 0x80)
            value >>= 7
        self.out.append(value)

    def s(self, value):
        self.u(value * 2 if value >= 0 else -value * 2 - 1)


def _strings(cell):
    table = {}
    def add(value):
        if not isinstance(value, str):
            raise ValueError('source strings and tags must be text')
        table.setdefault(value, len(table))
    for kind in KINDS:
        for feature in cell.get(kind, []):
            add(str(feature['id']))
            if 'kind' in feature:
                add(feature['kind'])
            for key, value in (feature.get('tags') or {}).items():
                add(str(key)); add(str(value))
            for node in feature.get('nodes', []):
                add(str(node))
    return table


def encode_cell(cell, origin):
    """Bytes of one compiled cell; origin is the cell's south-west corner in region metres."""
    w, strings = _Writer(), _strings(cell)
    w.u(len(strings))
    for text in strings:
        data = text.encode('utf8')
        w.u(len(data)); w.out += data
    ox, oy = _cm(origin[0]), _cm(origin[1])

    def ref(value):
        w.u(strings[str(value)])

    def tags(value):
        value = value or {}
        w.u(len(value))
        for key in sorted(value):
            ref(str(key)); ref(str(value[key]))

    def line(points, minimum):
        if len(points) < minimum:
            raise ValueError('too few source coordinates')
        w.u(len(points))
        px, py, pz = ox, oy, 0
        for p in points:
            if len(p) not in (2, 3):
                raise ValueError('source points need two or three coordinates')
            x, y, z = _cm(p[0]), _cm(p[1]), _cm(p[2] if len(p) == 3 else 0.)
            w.s(x - px); w.s(y - py); w.s(z - pz)
            px, py, pz = x, y, z

    def rings(value):
        if not value:
            raise ValueError('source polygon without rings')
        w.u(len(value))
        for ring in value:
            line(ring, 3)

    terrain = cell['terrain']
    heights = terrain['heights']
    if len(heights) != terrain['nx'] * terrain['ny']:
        raise ValueError('terrain size does not match its grid')
    w.u(terrain['nx']); w.u(terrain['ny'])
    previous = 0
    for h in heights:
        h = round(float(h) * TERRAIN)
        w.s(h - previous)
        previous = h
    for kind in KINDS:
        features = cell.get(kind, [])
        w.u(len(features))
        for f in features:
            ref(f['id'])
            if kind == 'roads':
                w.u(_cm(f['width'])); tags(f.get('tags'))
                w.u(len(f.get('nodes', [])))
                for node in f.get('nodes', []):
                    ref(node)
                line(f['points'], 2)
            elif kind == 'junctions':
                w.s(_cm(f['height'])); rings(f['rings'])
            elif kind == 'buildings':
                ref(f['kind']); w.u(_cm(f['height'])); w.u(_cm(f.get('minHeight', 0.)))
                tags(f.get('tags')); rings(f['rings'])
            elif kind == 'areas':
                ref(f['kind']); tags(f.get('tags')); rings(f['rings'])
            elif kind == 'lines':
                ref(f['kind']); tags(f.get('tags')); line(f['points'], 2)
            else:
                ref(f['kind']); tags(f.get('tags')); line([f['pos']], 1)
    return bytes(w.out)


class _Reader:
    def __init__(self, data, at=0, end=None):
        self.data, self.at, self.end = data, at, len(data) if end is None else end

    def u(self):
        value, shift = 0, 0
        while True:
            if self.at >= self.end or shift > 56:
                raise ValueError('truncated or invalid source varint')
            b = self.data[self.at]
            self.at += 1
            value |= (b & 0x7f) << shift
            if b < 0x80:
                return value
            shift += 7

    def s(self):
        value = self.u()
        return value >> 1 if value % 2 == 0 else -((value + 1) >> 1)


def decode_cell(data, origin, rect, at=0, end=None):
    """The table Lua builds from encode_cell's bytes (reference decoder for tests)."""
    r = _Reader(data, at, end)
    strings = []
    for _ in range(r.u()):
        n = r.u()
        if r.at + n > r.end:
            raise ValueError('truncated source string')
        strings.append(bytes(data[r.at:r.at + n]).decode('utf8'))
        r.at += n
    ref = lambda: strings[r.u()]
    ox, oy = _cm(origin[0]), _cm(origin[1])

    def tags():
        return {ref(): ref() for _ in range(r.u())}

    def line():
        out, px, py, pz = [], ox, oy, 0
        for _ in range(r.u()):
            px += r.s(); py += r.s(); pz += r.s()
            out.append([px / SCALE, py / SCALE, pz / SCALE])
        return out

    def rings():
        return [line() for _ in range(r.u())]

    nx, ny = r.u(), r.u()
    heights, h = [], 0
    for _ in range(nx * ny):
        h += r.s()
        heights.append(h / TERRAIN)
    cell = {'terrain': {'rect': list(rect), 'nx': nx, 'ny': ny, 'heights': heights}}
    for kind in KINDS:
        features = cell[kind] = []
        for _ in range(r.u()):
            f = {'id': ref()}
            if kind == 'roads':
                f['width'] = r.u() / SCALE; f['tags'] = tags()
                f['nodes'] = [ref() for _ in range(r.u())]
                f['points'] = line()
            elif kind == 'junctions':
                f['height'] = r.s() / SCALE; f['rings'] = rings()
            elif kind == 'buildings':
                f['kind'] = ref(); f['height'] = r.u() / SCALE; f['minHeight'] = r.u() / SCALE
                f['tags'] = tags(); f['rings'] = rings()
            elif kind == 'areas':
                f['kind'] = ref(); f['tags'] = tags(); f['rings'] = rings()
            elif kind == 'lines':
                f['kind'] = ref(); f['tags'] = tags(); f['points'] = line()
            else:
                f['kind'] = ref(); f['tags'] = tags(); f['pos'] = line()[0]
            features.append(f)
    if r.at != r.end:
        raise ValueError('trailing bytes after source cell')
    return cell


def slot(i, j):
    return (i % BLOCK) + (j % BLOCK) * BLOCK


def encode_block(payloads):
    """payloads: {slot: bytes}. Returns the block file and its 64-bit presence mask (hex)."""
    w = _Writer()
    w.out += MAGIC
    slots = sorted(payloads)
    w.u(len(slots))
    for k in slots:
        if not 0 <= k < BLOCK * BLOCK:
            raise ValueError('cell slot outside its block')
        w.u(k); w.u(len(payloads[k]))
    for k in slots:
        w.out += payloads[k]
    return bytes(w.out), mask(slots)


def mask(slots):
    """16 hex digits; digit d (first = 0) holds slots 4d..4d+3 as bits 1, 2, 4, 8."""
    digits = [0] * (BLOCK * BLOCK // 4)
    for k in slots:
        digits[k // 4] |= 1 << (k % 4)
    return ''.join('0123456789abcdef'[d] for d in digits)


def decode_block(data):
    """{slot: (start, end)} byte ranges of the cells of a block file."""
    if bytes(data[:4]) != MAGIC:
        raise ValueError('not an EarthNG source block')
    r = _Reader(data, 4)
    entries = [(r.u(), r.u()) for _ in range(r.u())]
    out, at = {}, r.at
    for k, n in entries:
        out[k] = (at, at + n)
        at += n
    if at != len(data):
        raise ValueError('source block size mismatch')
    return out
