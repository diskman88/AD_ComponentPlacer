"""Independent placement worker. No Altium API, no third-party dependencies."""
import math
import random
import time
from pathlib import Path


class Cancelled(Exception):
    pass


def number(s):
    n = float(s.replace(',', '.'))
    if not math.isfinite(n):
        raise ValueError('Non-finite coordinate')
    return n


def read_snapshot(path):
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'utf-16', 'mbcs', 'gb18030'):
        try:
            lines = raw.decode(enc).splitlines()
            if lines and lines[0] == 'CPBRIDGE2':
                break
        except (UnicodeError, LookupError):
            continue
    else:
        raise ValueError('Invalid snapshot encoding or version')
    it = iter(lines)
    assert next(it) == 'CPBRIDGE2'
    board, anchor = next(it), next(it)
    gap, radius = number(next(it)), number(next(it))
    n = int(next(it))
    if not 2 <= n <= 128 or not 0 <= gap <= 1000 or not 50 <= radius <= 10000:
        raise ValueError('Invalid settings or component count')
    parts = []
    for _ in range(n):
        name = next(it)
        pose = tuple(number(next(it)) for _ in range(3))
        box = tuple(number(next(it)) for _ in range(4))
        fixed = int(next(it))
        if box[0] > box[2] or box[1] > box[3] or fixed not in (0, 1):
            raise ValueError('Invalid footprint')
        parts.append(dict(name=name, pose=pose, box=box, fixed=bool(fixed)))
    if len({p['name'] for p in parts}) != n:
        raise ValueError('Duplicate designator')
    if anchor not in [p['name'] for p in parts]:
        raise ValueError('Missing anchor')
    ai = next(i for i, p in enumerate(parts) if p['name'] == anchor)
    if not parts[ai]['fixed']:
        raise ValueError('Anchor must be fixed')
    count = int(next(it))
    if not 0 <= count <= 8192:
        raise ValueError('Invalid obstacle count')
    obstacles = [tuple(number(next(it)) for _ in range(4)) for _ in range(count)]
    count = int(next(it))
    if not 0 <= count <= 4096:
        raise ValueError('Invalid pad count')
    pads = []
    for _ in range(count):
        i, net = int(next(it)), next(it)
        x, y = number(next(it)), number(next(it))
        if not 0 <= i < n:
            raise ValueError('Invalid pad owner')
        pads.append((i, net, x, y))
    if list(it):
        raise ValueError('Unexpected snapshot data')
    return dict(lines=lines, board=board, anchor=ai, parts=parts, pads=pads,
                obstacles=obstacles, gap=gap, radius=radius)


def rotate(x, y, angle):
    a = math.radians(angle)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def box_at(part, pose):
    x, y, a = pose
    l, b, r, t = part['box']
    corners = [rotate(u, v, a - part['pose'][2]) for u in (l, r) for v in (b, t)]
    return (x + min(v[0] for v in corners), y + min(v[1] for v in corners),
            x + max(v[0] for v in corners), y + max(v[1] for v in corners))


def intersect(a, b, gap):
    l, d, r, t = a
    x, y, z, w = b
    return l < z + gap - .001 and r > x - gap + .001 and d < w + gap - .001 and t > y - gap + .001


def legal(data, poses, i, pose):
    # Existing locked/anchor geometry is immutable.  It may legitimately
    # violate today's clearance rule in an already placed board; do not reject
    # the entire batch for that historical condition.  Movable parts still
    # undergo the full boundary and collision test below.
    if data['parts'][i]['fixed']:
        return True
    box = box_at(data['parts'][i], pose)
    anchors = data.get('anchors',[data['anchor']])
    centers = [data['parts'][anchor]['pose'] for anchor in anchors]
    # A manually placed, locked center is an explicit placement anchor.  Its
    # followers must remain clear of other geometry, but must not be rejected
    # merely for leaving the generated Room/search envelope.
    if not data.get('unbounded', False):
        radius = data['radius']
        # One anchor preserves the v0.3 range.  Multiple anchors use their
        # shared envelope so a group can be placed between them instead of
        # around only one.
        left=min(p[0] for p in centers)-radius; right=max(p[0] for p in centers)+radius
        bottom=min(p[1] for p in centers)-radius; top=max(p[1] for p in centers)+radius
        if box[0] < left or box[2] > right or box[1] < bottom or box[3] > top:
            return False
    if any(intersect(box, b, data['gap']) for b in data['obstacles']):
        return False
    return not any(j != i and p is not None and intersect(box, box_at(data['parts'][j], p), data['gap'])
                   for j, p in enumerate(poses))


def validate(data, poses):
    if len(poses) != len(data['parts']):
        raise ValueError('Incomplete placement')
    for i, (part, pose) in enumerate(zip(data['parts'], poses)):
        if pose is None or len(pose) != 3 or not all(math.isfinite(v) for v in pose):
            raise ValueError('Invalid pose')
        if part['fixed'] and pose != part['pose']:
            raise ValueError('Fixed component moved')
        if not legal(data, poses, i, pose):
            raise ValueError('Clearance or boundary violation: ' + part['name'])


def wire_scores(data, poses):
    """Return electrical (GND de-emphasized) and raw HPWL, in that order."""
    nets = {}
    for i, net, x, y in data['pads']:
        if poses[i] is None:
            continue
        px, py, a = poses[i]
        dx, dy = rotate(x, y, a-data['parts'][i]['pose'][2])
        x, y = px+dx, py+dy
        if net not in nets:
            nets[net] = [x, y, x, y, i, False]
        else:
            v = nets[net]
            v[0], v[1], v[2], v[3] = min(v[0], x), min(v[1], y), max(v[2], x), max(v[3], y)
            v[5] |= v[4] != i
    raw = sum(v[2]-v[0]+v[3]-v[1] for v in nets.values() if v[5])
    electrical = sum((v[2]-v[0]+v[3]-v[1]) * (.5 if net.upper() == 'GND' else 1)
                     for net, v in nets.items() if v[5])
    return electrical, raw


def center_pull(data, poses):
    anchors = data.get('anchors',[data['anchor']])
    centers = [data['parts'][anchor]['pose'] for anchor in anchors]
    return sum(min(abs(p[0]-cx)+abs(p[1]-cy) for cx,cy,_ in centers)
               for p in poses if p is not None)


def score(data, poses, weighted=True):
    electrical, raw = wire_scores(data, poses)
    return electrical + .08 * center_pull(data, poses) if weighted else raw


def alignment_cost(data, poses):
    """Smaller is tidier: components sharing a row or column cost zero."""
    total = 0.
    for i, pose in enumerate(poses):
        if pose is None or data['parts'][i]['fixed']:
            continue
        x, y, _ = pose
        distances = [min(abs(x-other[0]), abs(y-other[1]))
                     for j, other in enumerate(poses) if j != i and other is not None]
        if distances:
            total += min(distances)
    return total


def align_without_regression(data, poses, cancel=None):
    """Snap to existing rows/columns only when every placement priority holds.

    This deliberately runs after the electrical search.  A visually nicer row
    is never allowed to worsen weighted electrical HPWL, total HPWL, or the
    distance of followers from their selected center(s).
    """
    result = poses[:]
    movable = [i for i, part in enumerate(data['parts']) if not part['fixed']]
    electrical, routing = wire_scores(data, result)
    pull, beauty = center_pull(data, result), alignment_cost(data, result)
    epsilon = .000001
    for _ in range(3):
        changed = False
        for i in movable:
            if cancel is not None and cancel.is_set():
                raise Cancelled('Cancelled during alignment')
            x, y, angle = result[i]
            candidates = set()
            for j, other in enumerate(result):
                if i != j and other is not None:
                    candidates.add((other[0], y, angle))
                    candidates.add((x, other[1], angle))
            best_pose = None
            best_key = None
            for candidate in candidates:
                if candidate == result[i] or not legal(data, result, i, candidate):
                    continue
                trial = result[:]; trial[i] = candidate
                next_electrical, next_routing = wire_scores(data, trial)
                next_pull = center_pull(data, trial)
                next_beauty = alignment_cost(data, trial)
                if (next_electrical > electrical + epsilon or next_routing > routing + epsilon or
                        next_pull > pull + epsilon or next_beauty >= beauty - epsilon):
                    continue
                key = (next_beauty, next_electrical, next_routing, next_pull)
                if best_key is None or key < best_key:
                    best_key, best_pose = key, candidate
            if best_pose is not None:
                result[i] = best_pose
                electrical, routing = wire_scores(data, result)
                pull, beauty = center_pull(data, result), alignment_cost(data, result)
                changed = True
        if not changed:
            break
    return result


def solve(data, seconds=30, cancel=None, progress=lambda *args: None, seed=7):
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError('Time budget must be 0..300 seconds')
    start, rng = time.monotonic(), random.Random(seed)
    deadline = start + seconds
    def check():
        if cancel is not None and cancel.is_set():
            raise Cancelled('Cancelled; no new plan written')
        return time.monotonic() >= deadline
    def finish(poses):
        validate(data, poses)
        poses = align_without_regression(data, poses, cancel)
        validate(data, poses)
        progress(100, score(data, poses))
        return poses
    original = [p['pose'] for p in data['parts']]
    best, best_score = None, math.inf
    try:
        validate(data, original)
        best, best_score = original[:], score(data, original)
    except ValueError:
        pass
    anchors = data.get('anchors',[data['anchor']])
    centers = [original[anchor] for anchor in anchors]
    movable = [i for i, p in enumerate(data['parts']) if not p['fixed']]
    fixed_poses = [p['pose'] if p['fixed'] else None for p in data['parts']]
    # Do not preflight static geometry: locked components and selected anchors
    # are protected, rather than constraints the solver is allowed to repair.
    attempts, last, stale = 0, 0, 0
    while not check() and attempts < 25000:
        attempts += 1
        poses = best[:] if best is not None and attempts > 1 else fixed_poses[:]
        if any(p is None for p in poses):
            order = sorted(movable, key=lambda i: -(data['parts'][i]['box'][2]-data['parts'][i]['box'][0]) *
                           (data['parts'][i]['box'][3]-data['parts'][i]['box'][1]))
            for i in order:
                chosen, cost = None, math.inf
                for _ in range(350):
                    if check():
                        break
                    cx, cy, _ = rng.choice(centers)
                    radius = data['radius'] * rng.random()
                    angle = rng.random()*math.tau
                    pose = (round((cx+math.cos(angle)*radius)/5)*5,
                            round((cy+math.sin(angle)*radius)/5)*5, rng.randrange(4)*90.)
                    if legal(data, poses, i, pose):
                        poses[i] = pose
                        value = score(data, poses)
                        poses[i] = None
                        if value < cost:
                            chosen, cost = pose, value
                if chosen is None:
                    break
                poses[i] = chosen
            if any(p is None for p in poses):
                continue
        current = score(data, poses)
        if current < best_score:
            best, best_score = poses[:], current
        for k in range(1500):
            if check() or not movable:
                break
            stale += 1
            if best is not None and stale >= 500:
                return finish(best)
            i = rng.choice(movable)
            old = poses[i]
            step = 100*(1-k/1500)+5
            pose = (old[0]+round(rng.uniform(-step, step)/5)*5,
                    old[1]+round(rng.uniform(-step, step)/5)*5,
                    rng.randrange(4)*90. if rng.random() < .2 else old[2])
            if legal(data, poses, i, pose):
                poses[i] = pose
                value = score(data, poses)
                temp = 20*(1-k/1500)+.01
                if value < current or rng.random() < math.exp(max(-700, min(0, (current-value)/temp))):
                    current = value
                else:
                    poses[i] = old
                if current < best_score:
                    best, best_score = poses[:], current
                    stale = 0
            elapsed = time.monotonic()-start
            if elapsed-last >= .2:
                progress(min(99, int(elapsed/seconds*100)), best_score)
                last = elapsed
        if not movable:
            break
    check()
    if best is None:
        raise ValueError('No legal solution within time budget. Increase half-width or export fewer parts.')
    return finish(best)


def write_plan(data, poses, path):
    validate(data, poses)
    lines = data['lines'] + ['CPPLAN2', str(len(poses))]
    for part, pose in zip(data['parts'], poses):
        lines += [part['name']] + [format(v, '.12g') for v in pose]
    # UTF-8 BOM for TStringList on AD Windows; atomic publication.
    target = Path(path)
    tmp = target.with_suffix(target.suffix+'.tmp')
    tmp.write_text('\n'.join(lines)+'\n', encoding='utf-8-sig')
    tmp.replace(target)


def publish_plan(snapshot, original_bytes, data, poses, path, cancel):
    """Publication gate shared by the desktop and tests."""
    if cancel.is_set():
        raise Cancelled('Cancelled before publication')
    if Path(snapshot).read_bytes() != original_bytes:
        raise ValueError('Snapshot changed; export and calculate again')
    write_plan(data, poses, path)
