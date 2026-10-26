# -*- coding: utf-8 -*-
"""Job: установить стандартный вид активного 3D-документа и вписать модель.
PARAMS: {"view": "#Изометрия"}  (#Спереди, #Сзади, #Сверху, #Снизу, #Слева, #Справа)
Также снимает выделение (голубые контуры на скриншотах). После — kompas_screenshot."""
from win32com.client import Dispatch, gencache, CastTo
import pythoncom

m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
kompas = m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(
    m5.KompasObject.CLSID, pythoncom.IID_IDispatch))
m7 = gencache.EnsureModule("{69AC2981-37C0-4379-84FD-5DD2F3C0A520}", 0, 1, 0)
app7 = m7.IApplication(Dispatch("Kompas.Application.7")._oleobj_.QueryInterface(
    m7.IApplication.CLSID, pythoncom.IID_IDispatch))

doc5 = kompas.ActiveDocument3D()
doc7 = app7.ActiveDocument
assert doc5 is not None, "Активный документ — не 3D-модель"

view = PARAMS.get("view", "#Изометрия")
try:
    CastTo(doc7, "IKompasDocument3D").SelectionManager.UnselectAll()
except Exception:
    pass
vpc = doc5.GetViewProjectionCollection()
vp = vpc.GetByName(view, True, True)
assert vp is not None, f"Вид не найден: {view}"
vp.SetCurrent()
doc5.ZoomPrevNextOrAll(2)
print(f"Вид '{view}' установлен, модель вписана: {doc7.PathName or doc7.Name}")
