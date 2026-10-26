# Оставить видимыми только компоненты верхнего уровня из списка (точные имена или подстроки), остальное скрыть.
# PARAMS = {'keep_exact': [...], 'keep_sub': [...], 'restore': False}
from win32com.client import Dispatch, gencache, CastTo
import pythoncom
app7 = gencache.EnsureDispatch("Kompas.Application.7")
doc7 = app7.ActiveDocument
d3 = CastTo(doc7, "IKompasDocument3D")
top = d3.TopPart
exact = set(PARAMS.get('keep_exact', []))
subs = PARAMS.get('keep_sub', [])
shown = hidden = 0
for i in range(top.Parts.Count):
    p = top.Parts.Part(i)
    nm = p.Name
    vis = (nm in exact) or any(s in nm for s in subs)
    if p.Hidden == vis:
        p.Hidden = not vis
        p.Update()
    if vis: shown += 1
    else: hidden += 1
# спрятать плоскости/начало координат верхнего уровня
m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
kompas = m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(m5.KompasObject.CLSID, pythoncom.IID_IDispatch))
part = kompas.ActiveDocument3D().GetPart(-1)
for t in (1, 2, 3, 4, 71, 72, 73):
    try:
        e = part.GetDefaultEntity(t); e.hidden = True; e.Update()
    except Exception:
        pass
result = '%s: shown %d, hidden %d' % (doc7.Name, shown, hidden)
