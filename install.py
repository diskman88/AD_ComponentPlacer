"""One-time, relocatable setup; no registry edits, package installs, or AD settings changes."""
from pathlib import Path
import sys

def pascal_string(value):
    # Keep the generated AD unit ASCII even when the tool resides in a Chinese path.
    chunks=[];run=''
    for char in value:
        if ord(char)<127:
            run+=char
        else:
            if run:chunks.append("'"+run.replace("'","''")+"'");run=''
            chunks.append('#'+str(ord(char)))
    if run:chunks.append("'"+run.replace("'","''")+"'")
    return ''.join(chunks) or "''"

def install(root=None, executable=None):
    root=Path(root or __file__).resolve()
    if root.is_file(): root=root.parent
    python=Path(executable or sys.executable).resolve()
    pythonw=python.with_name('pythonw.exe')
    if pythonw.exists(): python=pythonw
    template=(root/'ClassPlacer.template').read_text(encoding='utf-8')
    script=template.replace('@PYTHON@',pascal_string(str(python))).replace('@RUNNER@',pascal_string(str(root/'class_desktop.py')))
    (root/'ClassPlacer.pas').write_text(script,encoding='ascii')
    print('Setup complete. Open:',root/'ClassPlacer_030.PrjScr')

if __name__=='__main__':
    import tkinter
    install()
