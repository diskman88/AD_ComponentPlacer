"""Drive Altium's File>Run Script dialog to run ClassPlacerSelfTest via UIA."""
import sys, time, os
import uiautomation as auto

LOG = []
def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s); print(s, flush=True)

def wait(cond, timeout=15, msg=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.3)
    log("TIMEOUT:", msg)
    return False

def find_dlg():
    dlg = auto.WindowControl(searchDepth=1, Name="Select Item To Run")
    if dlg.Exists(0, 0):
        return dlg
    return None

# 1. find the RunScript dialog
if not wait(lambda: find_dlg() is not None, 20, "Select Item To Run dialog"):
    print("RESULT: dialog not found"); sys.exit(1)
dlg = find_dlg()
log("dialog found", dlg.Name)

# 2. click Browse
browse = dlg.Control(searchDepth=20, AutomationId="btnBrowse")
if not browse.Exists(1, 1):
    print("RESULT: browse button not found"); sys.exit(1)
browse.SetFocus()
time.sleep(0.3)
browse.GetPattern(auto.PatternId.InvokePattern).Invoke()
time.sleep(0.8)

# 3. find the dropdown menu item "From file..." (overlay child of the main window)
item = None
def find_from_file():
    global item
    it = auto.GetRootControl().Control(searchDepth=30, Name="From file...")
    if it.Exists(0, 0):
        item = it
        return True
    return False
if not wait(find_from_file, 5, "From file... menu item"):
    print("RESULT: from-file item not found"); sys.exit(1)
log("from-file item found")
try:
    item.GetPattern(auto.PatternId.InvokePattern).Invoke()
    time.sleep(1.5)
except Exception as e:
    log("invoke failed, try real click:", e)
    try:
        item.Click(simulateMove=False)
    except Exception as e2:
        log("click fallback failed:", e2)

# 4. native file open dialog: locate it ("Select project to open" classic #32770)
filedlg = None
def find_filedlg():
    global filedlg
    d = auto.WindowControl(searchDepth=1, Name="Select project to open")
    if d.Exists(0, 0):
        filedlg = d
        return True
    return False
if not wait(find_filedlg, 10, "file open dialog"):
    print("RESULT: file dialog not found"); sys.exit(1)
log("file dialog found:", filedlg.Name, filedlg.ClassName)

# 5. type the filename into the classic dialog's filename edit
edit = None
for cand in (filedlg.EditControl(searchDepth=30, ControlId=1148),
             filedlg.EditControl(searchDepth=30, ControlId=1152)):
    if cand.Exists(1, 1):
        edit = cand
        break
if edit is None:
    edits = []
    def walk(c, depth=0):
        for ch in c.GetChildren():
            if ch.ControlTypeName == "EditControl":
                edits.append(ch)
            if depth < 8: walk(ch, depth + 1)
    walk(filedlg)
    log("edits found:", [(e.Name, e.ControlId) for e in edits])
    if not edits:
        print("RESULT: filename edit not found"); sys.exit(1)
    # filename edit is normally the deepest / the one inside a ComboBox near the Open button
    edit = edits[-1]
log("using edit:", edit.Name, edit.ControlId)
edit.SetFocus()
time.sleep(0.3)
auto.SetClipboardText(r"H:\AD_ClassPlacer_030\ClassPlacer_030.PrjScr")
auto.SendKeys("{Ctrl}v")
time.sleep(0.4)
log("typed path")
auto.SendKeys("{Enter}")
time.sleep(2.0)
log("opened prjscr")

# 6. back in RunScript dialog: find ClassPlacer nodes in tree
if not wait(lambda: dlg.Exists(0, 0), 5, "runscript dialog alive"):
    print("RESULT: runscript dialog gone"); sys.exit(1)
tree = dlg.Control(searchDepth=20, AutomationId="tvProcedures")
if not wait(lambda: tree.Exists(0, 0), 10, "tvProcedures"):
    print("RESULT: tree not found"); sys.exit(1)

node = tree.Control(searchDepth=20, Name="ClassPlacer_030.PrjScr")
if not wait(lambda: node.Exists(0, 0), 10, "ClassPlacer_030.PrjScr node"):
    # tree may not have refreshed; log current contents
    for ch in tree.GetChildren():
        log("  tree child:", ch.Name)
    print("RESULT: project node not found"); sys.exit(1)
log("project node found")
if not node.GetPattern(auto.PatternId.ExpandCollapsePattern).Expand():
    log("expand returned false")
time.sleep(0.5)
pas = tree.Control(searchDepth=20, Name="ClassPlacer.pas")
if not wait(lambda: pas.Exists(0, 0), 5, "ClassPlacer.pas node"):
    print("RESULT: pas node not found"); sys.exit(1)
log("pas node found")
try:
    pas.GetPattern(auto.PatternId.ExpandCollapsePattern).Expand()
except Exception as e:
    log("expand pas:", e)
time.sleep(0.5)
proc = tree.Control(searchDepth=20, Name="ClassPlacerSelfTest")
if not wait(lambda: proc.Exists(0, 0), 5, "ClassPlacerSelfTest node"):
    for ch in pas.GetChildren():
        log("  pas child:", ch.Name)
    print("RESULT: selftest node not found"); sys.exit(1)
proc.SetFocus()
proc.GetPattern(auto.PatternId.SelectionItemPattern).Select()
time.sleep(0.3)
log("selected ClassPlacerSelfTest")

ok = dlg.Control(searchDepth=20, AutomationId="btnOk")
ok.GetPattern(auto.PatternId.InvokePattern).Invoke()
log("clicked OK; waiting for result")
time.sleep(3)

# 7. check for error dialog / success file
err = auto.WindowControl(searchDepth=1, Name="Error")
if err.Exists(2, 1):
    text = ""
    try:
        txt = err.Control(searchDepth=20, foundIndex=1)
        text = ""
        for c in err.GetChildren():
            if c.ControlTypeName in ("TextControl", "EditControl"):
                text += c.Name + " | "
    except Exception:
        pass
    log("ERROR DIALOG APPEARED:", text or "(text unread)")
    # press OK to dismiss
    try:
        okb = err.ButtonControl(Name="OK")
        if okb.Exists(1, 1):
            okb.Invoke()
    except Exception:
        pass
    print("RESULT: COMPILE/RUN ERROR:", text)
else:
    log("no error dialog")
    if os.path.exists(r"H:\AD_ClassPlacer_030\selftest.txt"):
        print("RESULT: SUCCESS, selftest.txt written")
        with open(r"H:\AD_ClassPlacer_030\selftest.txt", encoding="utf-16", errors="ignore") as f:
            print(f.read())
    else:
        print("RESULT: no error dialog but selftest.txt missing")
