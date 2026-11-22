import copy
import json
import multiprocessing as mp
from pathlib import Path
import tempfile
import threading
import time
import unittest

from batch import ai_handoff_specs,load,center_options,center_indices,class_info,initial_radius,room_specs,select_classes,target_layers,plan_batch,validate_batch,publish
from solver import Cancelled,align_without_regression,box_at,intersect,legal,score,solve,validate,wire_scores
from class_desktop import work
from install import install


def fixture():
    def part(name,x,y,center,fixed=False):
        return dict(name=name,pose=(float(x),float(y),0.),box=(-10.,-10.,10.,10.),fixed=fixed,top=True,center=center,source_class='')
    parts=[part('U1',1000,1000,'U1'),part('C1',1600,1000,'U1'),part('M3',3000,3000,'U1',True),
           part('U2',2000,1000,'U2'),part('C2',2600,1000,'U2'),part('X9',5000,5000,'')]
    d=dict(board='C:\\设计\\test.PcbDoc',project='C:\\设计\\test.PrjPcb',parts=parts,
           pads=[(0,'A',0.,0.),(1,'A',0.,0.),(3,'B',0.,0.),(4,'B',0.,0.)],
           classes=[dict(name='POWER',members=[0,1,2]),dict(name='LCD',members=[3,4])])
    return d


def encode(d):
    lines=['CPCLASS3',d['board'],d['project'],str(len(d['parts']))]
    for p in d['parts']:
        lines += [p['name']]+[str(x) for x in p['pose']]+[str(x) for x in p['box']]+[str(int(p['fixed'])),str(int(p['top'])),p['center'],p['source_class']]
    lines += [str(len(d['pads']))]
    for i,net,x,y in d['pads']:lines += [str(i),net,str(x),str(y)]
    lines += [str(len(d['classes']))]
    for c in d['classes']:lines += [c['name'],str(len(c['members']))]+list(map(str,c['members']))
    d['lines']=lines
    return '\n'.join(lines)+'\n'


class BatchTests(unittest.TestCase):
    def test_roundtrip_unicode_and_gbk(self):
        for encoding in ('utf-8-sig','gb18030'):
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/'job';p.write_text(encode(fixture()),encoding=encoding)
                d=load(p);self.assertEqual(d['board'],fixture()['board'])
                self.assertEqual(len(d['parts']),6)
                self.assertEqual(class_info(d,d['classes'][0]),(0,''))

    def test_center_missing_conflict_and_outside_class(self):
        d=fixture();d['parts'][1]['center']=''
        self.assertIn('Missing',class_info(d,d['classes'][0])[1])
        d['parts'][1]['center']='U2'
        self.assertIn('Conflicting',class_info(d,d['classes'][0])[1])
        for i in [0,1,2]:d['parts'][i]['center']='U2'
        self.assertIn('member',class_info(d,d['classes'][0])[1])

    def test_shared_movable_classes_rejected(self):
        d=fixture();d['classes'].append(dict(name='COPY',members=[0,1,2]))
        with self.assertRaisesRegex(ValueError,'share movable'):select_classes(d,[0,2])

    def test_m3_outside_range_does_not_block(self):
        d=fixture();ids=[0,1,2]
        subset=dict(parts=copy.deepcopy(d['parts'][:3]),anchor=0,gap=12.,radius=120.,obstacles=[],pads=d['pads'][:2])
        subset['parts'][0]['fixed']=True
        poses=solve(subset,.15);validate(subset,poses)
        self.assertEqual(poses[2],d['parts'][2]['pose'])
        self.assertEqual(poses[0],d['parts'][0]['pose'])

    def test_all_classes_and_global_obstacles(self):
        d=fixture();poses,reports=plan_batch(d,[0,1],12.,1.,threading.Event())
        validate_batch(d,[0,1],poses,12.)
        for i in [0,2,3,5]:self.assertEqual(poses[i],d['parts'][i]['pose'])
        self.assertEqual(len(reports),2)
        self.assertLess(reports[0]['after_hpwl'],reports[0]['before_hpwl'])

    def test_unselected_class_does_not_move(self):
        d=fixture();poses,_=plan_batch(d,[0],12.,1.,threading.Event())
        for i in [2,3,4,5]:self.assertEqual(poses[i],d['parts'][i]['pose'])
        changed=list(poses);changed[4]=(1.,2.,0.)
        with self.assertRaisesRegex(ValueError,'Unauthorized'):validate_batch(d,[0],changed,12.)

    def test_adaptive_range_expands(self):
        d=fixture();d['classes']=d['classes'][:1];d['parts']=d['parts'][:3];d['pads']=d['pads'][:2]
        # An unchanged obstacle surrounds the center; movable C1 has to escape its rectangle.
        d['parts'].append(dict(name='BIG',pose=(1000.,1000.,0.),box=(-140.,-140.,140.,140.),fixed=True,top=True,center='',source_class=''))
        # The center itself overlaps this obstacle, so replace with four walls with free center.
        d['parts'].pop()
        for k,(x,y,box) in enumerate([(1000,1110,(-140,-10,140,10)),(1000,890,(-140,-10,140,10)),(1110,1000,(-10,-100,10,100)),(890,1000,(-10,-100,10,100))]):
            d['parts'].append(dict(name='W'+str(k),pose=(float(x),float(y),0.),box=box,fixed=True,top=True,center='',source_class=''))
        # Larger movable footprint cannot fit in the inner cavity with clearance.
        d['parts'][1]['box']=(-80.,-80.,80.,80.)
        from unittest.mock import patch
        with patch('batch.initial_radius',return_value=100.):
            poses,reports=plan_batch(d,[0],12.,2.,threading.Event())
        self.assertGreater(reports[0]['attempts'],1)
        validate_batch(d,[0],poses,12.)

    def test_cancel_and_stale_never_publish(self):
        d=fixture();event=threading.Event();event.set()
        with self.assertRaises(Cancelled):plan_batch(d,[0],12.,1.,event)
        event.clear();poses,_=plan_batch(d,[0],12.,1.,event)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'job';p.write_text(encode(d),encoding='utf-8-sig');raw=p.read_bytes()
            p.write_bytes(raw+b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):publish(d,[0],poses,12.,p,raw,event)
            self.assertFalse(Path(str(p)+'.plan').exists())
            p.write_bytes(raw);publish(d,[0],poses,12.,p,raw,event)
            result=Path(str(p)+'.plan').read_text(encoding='utf-8-sig').splitlines()
            self.assertEqual(result[:len(d['lines'])],d['lines'])
            self.assertEqual(result[len(d['lines'])],'CPCLASSPLAN5')
            self.assertEqual(len(result),len(d['lines'])+3+4+1+len(poses)*5)
            handoff=json.loads(Path(str(p)+'.ai-handoff.json').read_text(encoding='utf-8'))
            self.assertEqual(handoff['schema'],'ad-component-placer-ai-handoff/v1')
            self.assertEqual(handoff['groups'][0]['class_name'],'POWER')

    def test_process_worker(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'job';p.write_text(encode(fixture()),encoding='utf-8-sig')
            ctx=mp.get_context('spawn')
            cancel,events=ctx.Event(),ctx.Queue()
            proc=ctx.Process(target=work,args=(str(p),[0,1],12.,1.,cancel,events));proc.start()
            try:
                while True:
                    msg=events.get(timeout=8)
                    if msg[0]!='progress':break
                self.assertEqual(msg[0],'result',msg)
                proc.join(2);self.assertFalse(proc.is_alive())
                self.assertFalse(Path(str(p)+'.plan').exists())
            finally:
                if proc.is_alive():proc.terminate();proc.join()

    def test_live_spawn_process_cancellation(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'job';p.write_text(encode(fixture()),encoding='utf-8-sig')
            ctx=mp.get_context('spawn');cancel,events=ctx.Event(),ctx.Queue()
            proc=ctx.Process(target=work,args=(str(p),[0,1],12.,30.,cancel,events));proc.start()
            try:
                self.assertEqual(events.get(timeout=5)[0],'progress');cancel.set()
                while True:
                    msg=events.get(timeout=3)
                    if msg[0]!='progress':break
                self.assertEqual(msg[0],'cancelled');proc.join(2)
                self.assertFalse(proc.is_alive());self.assertFalse(Path(str(p)+'.plan').exists())
            finally:
                if proc.is_alive():proc.terminate();proc.join()

    def test_range_estimate_responds_to_size_and_count(self):
        parts=fixture()['parts'][:2]
        r=initial_radius(parts,12.)
        large=copy.deepcopy(parts)
        for p in large:p['box']=tuple(x*10 for x in p['box'])
        self.assertGreater(initial_radius(large,12.),r)
        self.assertGreater(initial_radius(parts*100,12.),r)

    def test_bad_coordinates_and_bottom(self):
        d=fixture();d['parts'][1]['top']=False
        self.assertEqual(class_info(d,d['classes'][0]),(0,''))
        poses=[p['pose'] for p in d['parts']];poses[1]=(float('nan'),0.,0.)
        with self.assertRaises(ValueError):validate_batch(d,[1],poses,12.)

    def test_manual_center_selection_overrides_missing_metadata(self):
        d=fixture();d['parts'][1]['center']=''
        self.assertIn('Missing',class_info(d,d['classes'][0])[1])
        self.assertEqual(center_options(d,d['classes'][0]),['U1','C1','M3'])
        self.assertEqual(class_info(d,d['classes'][0],'C1'),(1,''))
        poses,reports=plan_batch(d,[0],12.,1.,threading.Event(),centers={0:'C1'})
        self.assertEqual(poses[1],d['parts'][1]['pose'])
        self.assertEqual(reports[0]['center'],'C1')

    def test_multiple_centers_from_parameter_and_manual_selection(self):
        d=fixture()
        for i in d['classes'][0]['members']: d['parts'][i]['center']='U1 C1'
        self.assertEqual(center_indices(d,d['classes'][0]),([0,1],''))
        poses,reports=plan_batch(d,[0],12.,1.,threading.Event())
        self.assertEqual(poses[0],d['parts'][0]['pose'])
        self.assertEqual(poses[1],d['parts'][1]['pose'])
        self.assertEqual(reports[0]['centers'],['U1','C1'])
        self.assertEqual(center_indices(d,d['classes'][0],['C1 U1']),([1,0],''))
        with self.assertRaisesRegex(ValueError,'Duplicate CenterComponent'):
            for i in d['classes'][0]['members']: d['parts'][i]['center']='U1 U1'
            plan_batch(d,[0],12.,1.,threading.Event())

    def test_existing_locked_overlap_does_not_reject_batch(self):
        d=fixture();d['parts'][2]['pose']=(1000.,1000.,0.)
        poses,_=plan_batch(d,[0],12.,1.,threading.Event())
        self.assertEqual(poses[0],d['parts'][0]['pose'])
        self.assertEqual(poses[2],d['parts'][2]['pose'])

    def test_side_assignment_and_protected_center(self):
        d=fixture();d['parts'][1]['top']=False
        self.assertEqual(target_layers(d,[0],sides={0:'top'})[1],True)
        poses,reports=plan_batch(d,[0],12.,1.,threading.Event(),sides={0:'top'})
        self.assertEqual(reports[0]['side'],'top')
        validate_batch(d,[0],poses,12.,sides={0:'top'})
        with self.assertRaisesRegex(ValueError,'all centers must already'):
            target_layers(d,[0],sides={0:'bottom'})

    def test_v5_plan_contains_centers_side_and_layer_flags(self):
        d=fixture();d['parts'][1]['top']=False
        poses,_=plan_batch(d,[0],12.,1.,threading.Event(),centers={0:'U1'},sides={0:'top'})
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'job';p.write_text(encode(d),encoding='utf-8-sig')
            publish(d,[0],poses,12.,p,p.read_bytes(),threading.Event(),{0:'U1'},{0:'top'})
            lines=Path(str(p)+'.plan').read_text(encoding='utf-8-sig').splitlines()
            base=len(d['lines']);self.assertEqual(lines[base:base+7],['CPCLASSPLAN5','12.0','1','0','top','1','U1'])
            pose_base=base+8
            self.assertEqual(lines[pose_base+4],'1')
            self.assertEqual(lines[pose_base+5+4],'1')
            rooms=json.loads(Path(str(p)+'.rooms.json').read_text(encoding='utf-8'))
            self.assertEqual(rooms[0]['name'],'ClassPlacer_POWER')
            self.assertEqual(rooms[0]['center'],'U1')
            self.assertEqual(rooms[0]['layer'],'top')

    def test_room_specs_follow_final_member_bounds(self):
        d=fixture();poses=[p['pose'] for p in d['parts']]
        poses[1]=(1200.,1100.,0.)
        room=room_specs(d,[0],poses,12.)[0]
        self.assertEqual(room['members'],['U1','C1','M3'])
        self.assertEqual(room['bounds'],dict(left=978.,bottom=978.,right=3022.,top=3022.))
        self.assertTrue(room['room_enabled'])
        self.assertFalse(room['follow_locked_center'])

    def test_ai_handoff_preserves_pre_layout_blocks(self):
        d=fixture();poses=[p['pose'] for p in d['parts']]
        poses[1]=(1200.,1100.,90.)
        handoff=ai_handoff_specs(d,[0],poses,12.)
        self.assertEqual(handoff['schema'],'ad-component-placer-ai-handoff/v1')
        self.assertEqual(handoff['groups'][0]['preserve'],'rigid_first')
        c1=next(member for member in handoff['groups'][0]['members'] if member['designator']=='C1')
        self.assertEqual((c1['dx'],c1['dy']),(200.,100.))
        self.assertIn('APPLIED',handoff['invariant'])

    def test_locked_center_is_room_free_but_still_avoids_obstacles(self):
        d=fixture();d['parts'][0]['fixed']=True
        # This obstacle blocks C1's original side of U1.  Its legal new pose is
        # deliberately outside a tiny normal search envelope.
        d['parts'].append(dict(name='BLOCK',pose=(1120.,1000.,0.),box=(-100.,-100.,100.,100.),fixed=True,top=True,center='',source_class=''))
        d['classes']=d['classes'][:1];d['parts'][1]['box']=(-20.,-20.,20.,20.)
        local_parts=copy.deepcopy(d['parts'][:3])
        local_poses=[p['pose'] for p in local_parts]
        candidate=(1350.,1000.,0.)
        local_poses[1]=candidate
        room_bound=dict(parts=local_parts,anchor=0,anchors=[0],gap=12.,radius=100.,pads=[],
                        obstacles=[box_at(d['parts'][-1],d['parts'][-1]['pose'])])
        self.assertFalse(legal(room_bound,local_poses,1,candidate))
        room_bound['unbounded']=True
        self.assertTrue(legal(room_bound,local_poses,1,candidate))
        poses,reports=plan_batch(d,[0],12.,1.,threading.Event())
        self.assertTrue(reports[0]['follow_locked_center'])
        self.assertFalse(reports[0]['room_enabled'])
        self.assertEqual(poses[0],d['parts'][0]['pose'])
        boxes=[box_at(p,pose) for p,pose in zip(d['parts'],poses)]
        self.assertFalse(intersect(boxes[1],boxes[-1],12.))
        room=room_specs(d,[0],poses,12.)[0]
        self.assertFalse(room['room_enabled'])
        self.assertTrue(room['follow_locked_center'])

    def test_alignment_never_regresses_electrical_or_routing_score(self):
        d=fixture();parts=copy.deepcopy(d['parts'][:2])
        parts[0]['fixed']=True
        subset=dict(parts=parts,anchor=0,anchors=[0],gap=12.,radius=1000.,obstacles=[],pads=d['pads'][:2])
        poses=[parts[0]['pose'],(1300.,1100.,0.)]
        before_electrical,before_routing=wire_scores(subset,poses)
        before=score(subset,poses)
        aligned=align_without_regression(subset,poses)
        after_electrical,after_routing=wire_scores(subset,aligned)
        self.assertLessEqual(after_electrical,before_electrical)
        self.assertLessEqual(after_routing,before_routing)
        self.assertLessEqual(score(subset,aligned),before)
        validate(subset,aligned)

    def test_installer_quotes_paths_and_form_contract(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"user's tool";p.mkdir()
            root=Path(__file__).parent
            (p/'ClassPlacer.template').write_text((root/'ClassPlacer.template').read_text())
            install(p,Path(td)/"user's python.exe")
            script=(p/'ClassPlacer.pas').read_text(encoding='utf-8-sig')
            self.assertNotIn('@PYTHON@',script);self.assertIn("user''s",script)
            self.assertIn('Procedure RunClassPlacement;',script)
            self.assertNotIn('InputQuery',script)


if __name__=='__main__':
    mp.set_start_method('spawn',force=True)
    unittest.main(verbosity=2)
