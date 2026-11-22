"""Launched by AD. Class selection is the only normal per-run user action."""
import json
import logging
import multiprocessing as mp
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox
from batch import load,center_options,center_indices,select_classes,plan_batch,publish
from solver import Cancelled


def atomic_text(path,text):
    path=Path(path); tmp=Path(str(path)+'.tmp')
    tmp.write_text(text,encoding='utf-8-sig'); tmp.replace(path)


def work(snapshot,indices,gap,budget,cancel,events,centers=None,sides=None):
    logging.basicConfig(filename=str(snapshot)+'.worker.log',level=logging.INFO,encoding='utf-8',
                        format='%(asctime)s %(levelname)s %(message)s',force=True)
    try:
        data=load(snapshot)
        def progress(done,total,name,radius,stage):
            events.put(('progress',done,total,name,radius,stage))
        logging.info('Classes=%s, centers=%s, sides=%s, gap=%s, seconds/class=%s',indices,centers,sides,gap,budget)
        poses,reports=plan_batch(data,indices,gap,budget,cancel,progress,centers,sides)
        events.put(('result',poses,reports))
        logging.info('All selected classes solved and globally validated')
    except Cancelled:
        events.put(('cancelled',))
    except Exception as exc:
        logging.exception('Batch failed'); events.put(('error',str(exc)))


class App:
    def __init__(self,root,snapshot):
        self.root=root; self.path=Path(snapshot).resolve(); self.data=load(self.path)
        self.original=self.path.read_bytes(); self.proc=None; self.finished=False; self.ready=False
        self.cancel=None; self.cancel_at=None; self.last_heartbeat=0; self.dead_since=None
        root.title('Class Placer 0.4 — Class 与层分配'); root.geometry('980x600')
        ttk.Label(root,text='选择要布局的 Class 与目标层。中心器件由原理图 CenterComponent 参数自动确定；所有中心和锁定器件始终原位。若中心已锁定，外围器件跟随中心且不受 ClassPlacer Room 限制。',wraplength=940).pack(padx=16,pady=12)
        ttk.Label(root,text=self.data['board'],wraplength=800).pack(padx=16,anchor='w')
        body=ttk.Frame(root);body.pack(fill='both',expand=True,padx=16,pady=10)
        canvas=tk.Canvas(body,highlightthickness=0);scroll=ttk.Scrollbar(body,orient='vertical',command=canvas.yview)
        rows=ttk.Frame(canvas);rows.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0,0),window=rows,anchor='nw');canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.choices=[];self.center_boxes=[];self.side_vars=[];self.checks=[];self.manual_centers={}
        for i,cls in enumerate(self.data['classes']):
            options=center_options(self.data,cls);anchors,error=center_indices(self.data,cls)
            var=tk.BooleanVar(value=bool(options));side=tk.StringVar(value='keep')
            row=ttk.Frame(rows);row.pack(fill='x',pady=4)
            label=f"{cls['name']}  · {len(cls['members'])} 件"
            button=ttk.Checkbutton(row,text=label,variable=var,state='normal' if options else 'disabled',width=30)
            button.pack(side='left',anchor='n')
            if not error:
                centers_box=ttk.Label(row,text='  '.join(self.data['parts'][anchor]['name'] for anchor in anchors),width=24)
                centers_box.pack(side='left',padx=(8,2),anchor='n')
                editable=False
                note='CenterComponent 自动：'+(' '.join(self.data['parts'][anchor]['name'] for anchor in anchors))
            else:
                centers_box=tk.Listbox(row,selectmode='multiple',exportselection=False,height=min(3,max(1,len(options))),width=18)
                for option in options: centers_box.insert('end',option)
                if options: centers_box.selection_set(0)
                else: centers_box.configure(state='disabled')
                centers_box.pack(side='left',padx=(8,2))
                self.manual_centers[i]=centers_box;editable=True
                note=('参数异常，请手选中心：'+error if options else '不可布局：Empty class')
            ttk.Label(row,text='目标层').pack(side='left',padx=(12,2),anchor='n')
            side_box=ttk.Combobox(row,textvariable=side,values=('keep','top','bottom'),state='readonly' if options else 'disabled',width=10)
            side_box.pack(side='left',anchor='n')
            ttk.Label(row,text=note).pack(side='left',padx=10,anchor='n')
            self.choices.append(var);self.center_boxes.append(centers_box);self.side_vars.append(side)
            self.checks.append((button,centers_box,side_box,bool(options),editable))
        if not self.choices:ttk.Label(rows,text='没有找到非系统 Component Class。先从原理图同步到 PCB。').pack()
        controls=ttk.Frame(root);controls.pack(fill='x',padx=16)
        self.gap=tk.StringVar(value='12');self.budget=tk.StringVar(value='5')
        ttk.Label(controls,text='间距 mil').pack(side='left');ttk.Entry(controls,textvariable=self.gap,width=6).pack(side='left',padx=5)
        ttk.Label(controls,text='每类时限 秒').pack(side='left');ttk.Entry(controls,textvariable=self.budget,width=6).pack(side='left',padx=5)
        self.start_button=ttk.Button(controls,text='布局并自动应用',command=self.start);self.start_button.pack(side='right')
        self.cancel_button=ttk.Button(controls,text='取消',command=self.stop);self.cancel_button.pack(side='right',padx=8)
        self.bar=ttk.Progressbar(root,maximum=100);self.bar.pack(fill='x',padx=16,pady=10)
        self.status=ttk.Label(root,text='系统默认类已排除；CenterComponent 正常时无需选择中心。目标层必须与全部中心所在层一致。',wraplength=940)
        self.status.pack(fill='x',padx=16,pady=8)
        ttk.Label(root,text='布局范围自动估算与扩展。最终完成后 PCB 保持未保存，请检查并运行 DRC。',wraplength=800).pack(padx=16,pady=8)
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.state='CHOOSING';self.detail='Select component classes';self.write_status()
        root.after(100,self.poll)

    def write_status(self):
        atomic_text(str(self.path)+'.status',self.state+'\n'+self.detail+'\n'+str(time.time())+'\n')

    def start(self):
        if self.proc or self.ready or self.finished:return
        try:
            self.indices=[i for i,v in enumerate(self.choices) if v.get()]
            self.centers={i:[self.manual_centers[i].get(j) for j in self.manual_centers[i].curselection()]
                          for i in self.indices if i in self.manual_centers}
            self.sides={i:self.side_vars[i].get() for i in self.indices}
            select_classes(self.data,self.indices,self.centers,self.sides)
            self.gap_value=float(self.gap.get());self.budget_value=float(self.budget.get())
            if not 0<=self.gap_value<=1000 or not 1<=self.budget_value<=120:raise ValueError('间距 0–1000 mil；时限 1–120 秒/类')
            self.cancel=mp.Event();self.events=mp.Queue()
            self.proc=mp.Process(target=work,args=(str(self.path),self.indices,self.gap_value,self.budget_value,self.cancel,self.events,self.centers,self.sides),daemon=True)
            self.proc.start();self.started=time.monotonic();self.deadline=self.started+(self.budget_value+5)*len(self.indices)+10
            self.stage_done=0;self.stage_total=len(self.indices);self.class_started=self.started
            self.state='RUNNING';self.detail='Starting independent worker';self.status.config(text='工作进程已启动，正在初始化…')
            self.write_status();self.start_button.config(state='disabled')
            for b,c,s,_,editable in self.checks:
                b.config(state='disabled')
                if editable:c.config(state='disabled')
                s.config(state='disabled')
        except Exception as exc:messagebox.showerror('无法开始',str(exc))

    def stop(self):
        if self.finished:return
        atomic_text(str(self.path)+'.cancel','cancel\n')
        if self.cancel:self.cancel.set()
        self.cancel_at=time.monotonic();self.state='CANCELLED';self.detail='Cancelled; no batch applied'
        self.write_status();self.status.config(text='已取消；未应用批次。')
        self.finished=True

    def fail(self,text):
        self.state='ERROR';self.detail=text;self.write_status();self.status.config(text='失败：'+text)
        self.finished=True;messagebox.showerror('布局未应用',text)

    def end_worker(self):
        if self.proc:
            self.proc.join(.05)
            if self.proc.is_alive():self.proc.terminate();self.proc.join(1)
            self.proc=None

    def poll(self):
        try:
            if Path(str(self.path)+'.cancel').exists() and not self.finished:self.stop()
            if self.cancel_at and time.monotonic()-self.cancel_at>=1:self.end_worker();self.cancel_at=None
            if self.proc and not self.finished:
                try:
                    while self.proc:
                        msg=self.events.get_nowait()
                        if msg[0]=='progress':
                            _,done,total,name,radius,stage=msg
                            if done!=self.stage_done:self.class_started=time.monotonic()
                            self.stage_done,self.stage_total=done,total
                            self.bar['value']=done/max(1,total)*100
                            self.detail=f'{done}/{total}: {name}; radius {radius:.1f} mil; {stage}'
                            self.status.config(text=self.detail)
                        elif msg[0]=='result':
                            if self.cancel.is_set():self.stop();break
                            publish(self.data,self.indices,msg[1],self.gap_value,self.path,self.original,self.cancel,self.centers,self.sides)
                            atomic_text(str(self.path)+'.report.json',json.dumps(msg[2],ensure_ascii=False,indent=2))
                            self.ready=True;self.state='READY';self.detail='Validated plan ready; waiting for AD automatic apply'
                            self.write_status();self.status.config(text='计算完成，等待 AD 自动校验和应用…');self.end_worker()
                        elif msg[0]=='error':self.end_worker();self.fail(msg[1])
                        elif msg[0]=='cancelled':self.end_worker();self.stop()
                except queue.Empty:pass
                if self.proc:
                    now=time.monotonic()
                    fraction=min(.95,(now-self.class_started)/self.budget_value)
                    self.bar['value']=min(99,(self.stage_done+fraction)/max(1,self.stage_total)*100)
                    if now>self.deadline:self.end_worker();self.fail('超过工作进程硬时限；未应用。')
                    elif not self.proc.is_alive():
                        if self.dead_since is None:self.dead_since=now
                        elif now-self.dead_since>1:self.end_worker();self.fail('工作进程退出，请查看 .worker.log')
                    else:self.dead_since=None
            adstatus=Path(str(self.path)+'.adstatus')
            if adstatus.exists() and not self.finished:
                result=adstatus.read_text(encoding='utf-8-sig').strip()
                self.finished=True;self.bar['value']=100;self.status.config(text=result)
                self.cancel_button.config(text='关闭',command=self.close)
                if result.startswith('APPLIED'):messagebox.showinfo('布局完成','所有选择的类已应用，PCB 未保存。请检查布局和 DRC。')
                else:messagebox.showerror('AD 未应用布局',result)
            if time.monotonic()-self.last_heartbeat>1 and not self.finished:
                self.write_status();self.last_heartbeat=time.monotonic()
        except Exception as exc:self.end_worker();self.fail(str(exc))
        self.root.after(100,self.poll)

    def close(self):
        if not self.finished:self.stop()
        self.end_worker();self.root.destroy()


if __name__=='__main__':
    mp.freeze_support()
    snapshot=sys.argv[1]
    try:
        root=tk.Tk();App(root,snapshot);root.mainloop()
    except Exception as exc:
        atomic_text(str(snapshot)+'.status','ERROR\n'+str(exc)+'\n')
        raise
