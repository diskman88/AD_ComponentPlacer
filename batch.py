"""Class metadata, bounded adaptive placement, and atomic result publication."""
import copy
import json
import math
from pathlib import Path
import time
from solver import Cancelled, box_at, intersect, number, solve, validate, score


def read_lines(path):
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'utf-16', 'mbcs', 'gb18030'):
        try:
            lines = raw.decode(enc).splitlines()
            if lines and lines[0] == 'CPCLASS3': return lines
        except (UnicodeError, LookupError): pass
    raise ValueError('Invalid snapshot format or encoding')


def load(path):
    lines = read_lines(path); it = iter(lines)
    next(it); board, project = next(it), next(it)
    n = int(next(it))
    if not 1 <= n <= 2048: raise ValueError('Board component limit is 2048')
    parts = []
    for _ in range(n):
        name = next(it); pose = tuple(number(next(it)) for _ in range(3))
        box = tuple(number(next(it)) for _ in range(4))
        fixed, top = int(next(it)), int(next(it))
        center, source_class = next(it), next(it)
        if fixed not in (0,1) or top not in (0,1) or box[0]>box[2] or box[1]>box[3]:
            raise ValueError('Invalid footprint')
        parts.append(dict(name=name, pose=pose, box=box, fixed=bool(fixed), top=bool(top),
                          center=center, source_class=source_class))
    if len({p['name'] for p in parts}) != n: raise ValueError('Duplicate PCB designators')
    count = int(next(it))
    if not 0 <= count <= 65536: raise ValueError('Pad limit exceeded')
    pads = []
    for _ in range(count):
        i, net = int(next(it)), next(it); x, y = number(next(it)), number(next(it))
        if not 0 <= i < n: raise ValueError('Invalid pad owner')
        pads.append((i,net,x,y))
    count = int(next(it))
    if not 0 <= count <= 256: raise ValueError('Class limit exceeded')
    classes = []
    for _ in range(count):
        name = next(it); m = int(next(it))
        if not 0 <= m <= n: raise ValueError('Invalid member count')
        members = [int(next(it)) for _ in range(m)]
        if len(set(members)) != m or any(i<0 or i>=n for i in members): raise ValueError('Invalid class membership')
        classes.append(dict(name=name, members=members))
    if list(it): raise ValueError('Trailing snapshot data')
    return dict(lines=lines,board=board,project=project,parts=parts,pads=pads,classes=classes)


def center_options(data, cls):
    """Return selectable PCB designators for a component class, in class order."""
    return [data['parts'][i]['name'] for i in cls['members']]


def center_indices(data, cls, center_names=None):
    """Resolve one or more deliberately selected anchors for a class."""
    members = cls['members']; parts = data['parts']
    if not members: return None, 'Empty class'
    if center_names is not None:
        if isinstance(center_names, str): center_names = center_names.split()
        wanted = [token.upper() for name in center_names for token in str(name).split()]
        if not wanted: return None, 'Select at least one center component'
        if len(set(wanted)) != len(wanted): return None, 'Duplicate selected center component'
        matches = []
        for name in wanted:
            found = [i for i in members if parts[i]['name'].strip().upper() == name]
            if len(found) != 1: return None, 'Selected center must name a member of this class'
            matches.append(found[0])
        return matches, ''
    values = [tuple(token.upper() for token in parts[i]['center'].split()) for i in members]
    if any(not value for value in values): return None, 'Missing CenterComponent on one or more members'
    if len(set(values)) != 1: return None, 'Conflicting CenterComponent values'
    wanted = values[0]
    if len(set(wanted)) != len(wanted): return None, 'Duplicate CenterComponent designator'
    matches = []
    for name in wanted:
        found = [i for i in members if parts[i]['name'].strip().upper() == name]
        if len(found) != 1: return None, 'CenterComponent must name a member of this class'
        matches.append(found[0])
    return matches, ''


def class_info(data, cls, center_name=None):
    """Validate a class and resolve its anchor.

    ``center_name`` is the v0.4 UI override.  When it is supplied it is the
    user's deliberate selection and takes precedence over schematic metadata;
    this makes a missing/stale CenterComponent recoverable without editing a
    schematic just to try a placement.
    """
    anchors, error = center_indices(data,cls,center_name)
    return (anchors[0],error) if not error else (None,error)


def initial_radius(parts, gap):
    area = sum((p['box'][2]-p['box'][0]+gap)*(p['box'][3]-p['box'][1]+gap)
               for p in parts if not p['fixed'])
    largest = max([max(p['box'][2]-p['box'][0],p['box'][3]-p['box'][1])
                   for p in parts if not p['fixed']] or [0])
    return max(100., math.sqrt(area/.55)/2+largest/2+gap)


def _selection(data, indices, centers=None, sides=None):
    """Normalize/validate v0.4 per-class anchor and destination side choices."""
    if not indices or len(set(indices)) != len(indices): raise ValueError('Select at least one class without duplicates')
    centers = centers or {}; sides = sides or {}
    seen = set(); anchors = set()
    result = []
    for ci in indices:
        if not 0 <= ci < len(data['classes']): raise ValueError('Invalid class index')
        cls = data['classes'][ci]
        choice = centers.get(ci)
        selected_anchors, error = center_indices(data,cls,choice)
        if error: raise ValueError(cls['name']+': '+error)
        side = str(sides.get(ci, 'keep')).lower()
        if side not in ('keep','top','bottom'):
            raise ValueError(cls['name']+': invalid target side')
        if side != 'keep' and any(data['parts'][anchor]['top'] != (side == 'top') for anchor in selected_anchors):
            raise ValueError(cls['name']+': all centers must already be on the target side')
        moving = {i for i in cls['members'] if not data['parts'][i]['fixed']}
        if seen & moving: raise ValueError('Selected classes share movable members: '+cls['name'])
        seen |= moving; anchors.update(selected_anchors); result.append((ci,selected_anchors,side))
    return seen, anchors, result


def select_classes(data, indices, centers=None, sides=None):
    """Compatibility wrapper used by the selector and existing integrations."""
    seen, anchors, _ = _selection(data,indices,centers,sides)
    return seen, anchors


def target_layers(data, indices, centers=None, sides=None):
    """Return the final side for every component, rejecting protected flips."""
    _, anchors, choices = _selection(data,indices,centers,sides)
    target = [p['top'] for p in data['parts']]
    for ci, anchors_for_class, side in choices:
        if side == 'keep': continue
        wanted = side == 'top'
        for i in data['classes'][ci]['members']:
            if i not in anchors_for_class and not data['parts'][i]['fixed']:
                target[i] = wanted
    for i, part in enumerate(data['parts']):
        if (part['fixed'] or i in anchors) and target[i] != part['top']:
            raise ValueError('Protected component changes side: '+part['name'])
    return target


def room_specs(data, indices, poses, gap, centers=None, sides=None):
    """Build deterministic ClassPlacer Room definitions from the final poses.

    The AD bridge consumes the same selected-class records in the plan.  This
    sidecar is deliberately human-readable so a room update is auditable and
    can be replayed after an AD-side interruption.
    """
    layers = target_layers(data,indices,centers,sides)
    _, _, choices = _selection(data,indices,centers,sides)
    by_class = {ci:(anchors_for_class,side) for ci,anchors_for_class,side in choices}
    specs = []
    for ci in indices:
        cls = data['classes'][ci]; anchors_for_class, side = by_class[ci]
        follow_locked_center = any(data['parts'][i]['fixed'] for i in anchors_for_class)
        boxes = [box_at(data['parts'][i],poses[i]) for i in cls['members']]
        specs.append(dict(name='ClassPlacer_'+cls['name'],class_name=cls['name'],
                          center=data['parts'][anchors_for_class[0]]['name'],
                          centers=[data['parts'][i]['name'] for i in anchors_for_class],side=side,
                          layer='top' if layers[anchors_for_class[0]] else 'bottom',
                          room_enabled=not follow_locked_center,
                          follow_locked_center=follow_locked_center,
                          members=[data['parts'][i]['name'] for i in cls['members']],
                          bounds=dict(left=min(b[0] for b in boxes)-gap,
                                      bottom=min(b[1] for b in boxes)-gap,
                                      right=max(b[2] for b in boxes)+gap,
                                      top=max(b[3] for b in boxes)+gap)))
    return specs


def plan_batch(data, indices, gap, per_class, cancel, progress=lambda *args: None,
               centers=None, sides=None):
    if not math.isfinite(gap) or not 0 <= gap <= 1000: raise ValueError('Gap must be 0..1000 mil')
    if not math.isfinite(per_class) or not 1 <= per_class <= 120: raise ValueError('Budget must be 1..120 seconds/class')
    _, all_anchors, choices = _selection(data,indices,centers,sides)
    selected = {ci:(anchors_for_class,side) for ci,anchors_for_class,side in choices}
    final_layers = target_layers(data,indices,centers,sides)
    poses = [p['pose'] for p in data['parts']]; reports = []
    for order, ci in enumerate(indices):
        if cancel.is_set(): raise Cancelled()
        cls = data['classes'][ci]; ids = cls['members']; mapping = {i:j for j,i in enumerate(ids)}
        anchors_for_class, side = selected[ci]
        follow_locked_center = any(data['parts'][i]['fixed'] for i in anchors_for_class)
        parts = [copy.deepcopy(data['parts'][i]) for i in ids]
        for j, i in enumerate(ids):
            parts[j]['pose'] = poses[i]; parts[j]['fixed'] |= i in all_anchors
            parts[j]['top'] = final_layers[i]
        obstacles = [box_at(p,poses[i]) for i,p in enumerate(data['parts']) if i not in mapping]
        pads = [(mapping[i],net,x,y) for i,net,x,y in data['pads'] if i in mapping]
        subset = dict(parts=parts,anchor=mapping[anchors_for_class[0]],
                      anchors=[mapping[i] for i in anchors_for_class],gap=gap,pads=pads,obstacles=obstacles,
                      # A locked center is a user-established anchor, so this
                      # class follows it without a Room/search-boundary limit.
                      unbounded=follow_locked_center)
        radius = initial_radius(parts,gap); start = time.monotonic(); result = None; attempts = 0
        while time.monotonic()-start < per_class and radius <= 20000 and attempts<8:
            attempts += 1; subset['radius'] = radius
            remaining = per_class-(time.monotonic()-start)
            if remaining <= .01: break
            progress(order,len(indices),cls['name'],radius,'searching')
            try:
                result = solve(subset,min(max(.05,per_class/6),remaining),cancel,
                               lambda pct,cost: progress(order,len(indices),cls['name'],radius,'searching'),seed=7+attempts)
                break
            except ValueError as exc:
                if 'No legal solution' not in str(exc):
                    raise ValueError(cls['name']+': '+str(exc)) from exc
                radius *= 1.6
        if result is None: raise ValueError(cls['name']+': no solution within budget/expansion limit; nothing applied')
        validate(subset,result)
        before = score(subset,[p['pose'] for p in parts],False); after = score(subset,result,False)
        for j,i in enumerate(ids): poses[i] = result[j]
        reports.append(dict(name=cls['name'],center=data['parts'][anchors_for_class[0]]['name'],
                            centers=[data['parts'][i]['name'] for i in anchors_for_class],side=side,
                            room='ClassPlacer_'+cls['name'],radius=radius,attempts=attempts,
                            room_enabled=not follow_locked_center,
                            follow_locked_center=follow_locked_center,
                            before_hpwl=before,after_hpwl=after))
        progress(order+1,len(indices),cls['name'],radius,'finished')
    validate_batch(data,indices,poses,gap,centers,sides,final_layers)
    return poses,reports


def validate_batch(data, indices, poses, gap, centers=None, sides=None, layers=None):
    allowed, anchors = select_classes(data,indices,centers,sides)
    expected_layers = target_layers(data,indices,centers,sides) if layers is None else layers
    if len(expected_layers) != len(data['parts']): raise ValueError('Incomplete layer result')
    if len(poses) != len(data['parts']): raise ValueError('Incomplete result')
    changed = set()
    for i,(p,pose) in enumerate(zip(data['parts'],poses)):
        if len(pose)!=3 or not all(math.isfinite(v) for v in pose) or any(abs(v)>200000 for v in pose[:2]) or not 0<=pose[2]<=360:
            raise ValueError('Invalid placement coordinate')
        moved = tuple(pose) != tuple(p['pose'])
        if moved:
            if i not in allowed or i in anchors or p['fixed']: raise ValueError('Unauthorized move: '+p['name'])
            changed.add(i)
        if not isinstance(expected_layers[i], bool): raise ValueError('Invalid target layer')
        if expected_layers[i] != p['top']:
            if i not in allowed or i in anchors or p['fixed']: raise ValueError('Unauthorized layer change: '+p['name'])
            changed.add(i)
    boxes = [box_at(p,pose) for p,pose in zip(data['parts'],poses)]
    for i in changed:
        for j in range(len(poses)):
            if i!=j and intersect(boxes[i],boxes[j],gap):
                raise ValueError('Final collision: '+data['parts'][i]['name']+' / '+data['parts'][j]['name'])


def publish(data, indices, poses, gap, snapshot, original_bytes, cancel, centers=None, sides=None):
    layers = target_layers(data,indices,centers,sides)
    validate_batch(data,indices,poses,gap,centers,sides,layers)
    if cancel.is_set(): raise Cancelled()
    if Path(snapshot).read_bytes()!=original_bytes: raise ValueError('Snapshot changed')
    centers = centers or {}; sides = sides or {}
    lines = data['lines']+['CPCLASSPLAN5',str(gap),str(len(indices))]
    for ci in indices:
        anchors_for_class, error = center_indices(data,data['classes'][ci],centers.get(ci))
        if error: raise ValueError(data['classes'][ci]['name']+': '+error)
        lines += [str(ci),str(sides.get(ci,'keep')).lower(),str(len(anchors_for_class))]
        lines += [data['parts'][i]['name'] for i in anchors_for_class]
    lines += [str(len(poses))]
    for i,(p,pose) in enumerate(zip(data['parts'],poses)):
        # Preserve exact decimal source poses for unchanged/fixed parts.
        lines += [p['name']]+[format(v,'.17g') for v in pose]+['1' if layers[i] else '0']
    target=Path(str(snapshot)+'.plan'); temp=Path(str(target)+'.tmp')
    temp.write_text('\n'.join(lines)+'\n',encoding='utf-8-sig'); temp.replace(target)
    rooms_target=Path(str(snapshot)+'.rooms.json'); rooms_temp=Path(str(rooms_target)+'.tmp')
    rooms_temp.write_text(json.dumps(room_specs(data,indices,poses,gap,centers,sides),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rooms_temp.replace(rooms_target)
