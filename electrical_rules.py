"""Explicit electrical placement rules loaded beside the PCB document."""
import json
import math
from pathlib import Path


class ElectricalRuleError(ValueError):
    pass


def _rule(item, kind='pin_proximity', required_source_net=''):
    if not isinstance(item, dict): raise ElectricalRuleError('Each electrical rule must be an object')
    source, target = item.get('source'), item.get('target')
    source_net, target_net = item.get('source_net'), item.get('target_net')
    if not all(isinstance(value, str) and value.strip() for value in (source,target,source_net,target_net)):
        raise ElectricalRuleError('Each rule needs source, target, source_net and target_net')
    severity = str(item.get('severity','hard')).lower()
    if severity not in ('hard','soft'): raise ElectricalRuleError('severity must be hard or soft')
    try:
        maximum, weight = float(item.get('max_distance_mil',250)), float(item.get('weight',8))
    except (TypeError, ValueError) as exc: raise ElectricalRuleError('Distance and weight must be numeric') from exc
    if not all(math.isfinite(value) for value in (maximum,weight)) or not 0 < maximum <= 5000 or not 0 <= weight <= 100:
        raise ElectricalRuleError('Invalid electrical rule distance or weight')
    return dict(kind=kind,source=source.strip(),target=target.strip(),source_net=source_net.strip(),
                target_net=target_net.strip(),required_source_net=required_source_net.strip(),
                max_distance=maximum,weight=weight,severity=severity)


def _load_document(board_path):
    path = Path(board_path).with_name('ClassPlacerRules.json')
    if not path.exists(): return {}
    try: value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise ElectricalRuleError('Invalid ClassPlacerRules.json') from exc
    if not isinstance(value, dict) or value.get('schema') != 'ad-component-placer-rules/v1':
        raise ElectricalRuleError('Unsupported ClassPlacerRules.json schema')
    return value


def load_rules(board_path):
    """Return normalized pin-proximity rules; absence of a file is valid."""
    value = _load_document(board_path)
    rules = value.get('pin_proximity',[])
    legacy = value.get('decoupling',[])
    if not isinstance(rules,list) or not isinstance(legacy,list): raise ElectricalRuleError('Rule collections must be arrays')
    result = [_rule(item) for item in rules]
    for item in legacy:
        if not isinstance(item,dict): raise ElectricalRuleError('Each decoupling rule must be an object')
        translated=dict(item,source=item.get('capacitor'),target=item.get('ic'),
                        source_net=item.get('power_net'),target_net=item.get('power_net'))
        result.append(_rule(translated,'decoupling',str(item.get('ground_net',''))))
    return result


def load_manual_overrides(board_path):
    value = _load_document(board_path)
    entries = value.get('manual_overrides',[])
    if not isinstance(entries,list): raise ElectricalRuleError('manual_overrides must be an array')
    result = {}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('component'),str) or not entry['component'].strip():
            raise ElectricalRuleError('Each manual override needs a component')
        name = entry['component'].strip().upper()
        if name in result: raise ElectricalRuleError('Duplicate manual override: '+entry['component'])
        lock_position = entry.get('lock_position',False); preserve_rotation = entry.get('preserve_rotation',False)
        if not isinstance(lock_position,bool) or not isinstance(preserve_rotation,bool):
            raise ElectricalRuleError('Manual override booleans are invalid')
        try: max_shift = float(entry.get('max_shift_mil',0))
        except (TypeError, ValueError) as exc: raise ElectricalRuleError('max_shift_mil must be numeric') from exc
        if not math.isfinite(max_shift) or not 0 <= max_shift <= 20000: raise ElectricalRuleError('Invalid max_shift_mil')
        result[name] = dict(lock_position=lock_position,preserve_rotation=preserve_rotation,max_shift=max_shift)
    return result
