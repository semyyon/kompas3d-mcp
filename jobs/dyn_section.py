# Динамическое сечение активной 3D-модели плоскостью XOZ (+смещение) — только отображение, модель не меняется.
# PARAMS = {'mode': 'on'|'off', 'offset': 0.0, 'reverse': False, 'plane': 2}   (plane: 1=XOY, 2=XOZ, 3=YOZ)
from win32com.client import gencache, CastTo
app7 = gencache.EnsureDispatch("Kompas.Application.7")
doc7 = app7.ActiveDocument
d3 = CastTo(doc7, "IKompasDocument3D")
mgr = CastTo(d3, "IDynamicCrossSectionsManager")
mode = PARAMS.get('mode', 'on')
info = []
if mode == 'off':
    mgr.DynamicCrossSectionModeOn = False
    n = mgr.DynamicCrossSectionsCount
    for k in range(n - 1, -1, -1):
        try:
            s = mgr.DynamicCrossSection(k)
            if s is not None and s.Delete(): info.append('deleted %d' % k)
        except Exception as e:
            info.append('del %d err %s' % (k, str(e)[:60]))
else:
    sec = mgr.AddDynamicCrossSection()
    step = sec.AddStep(1)                       # ksDCSTByOffsetPlane
    prm = CastTo(step.Parameters, "IDynamicCrossSectionStepParametersByOffsetPlane")
    try: prm.PlaneBuildingType = 0              # ksCrossSectionPlaneByModelPlan
    except Exception as e: info.append('pbt ' + str(e)[:60])
    op = prm.OffsetPlane
    op.BasePlane = d3.TopPart.DefaultObject(PARAMS.get('plane', 2))
    op.Offset = float(PARAMS.get('offset', 0.0))
    if PARAMS.get('reverse'):
        info.append('rev=%s' % prm.ReverseDirection())
    try: sec.FillCutPlanes = True
    except Exception as e: info.append('fill ' + str(e)[:60])
    try: info.append('upd=%s' % sec.Update())
    except Exception as e: info.append('upd ' + str(e)[:60])
    try: mgr.CurrentDynamicCrossSection = sec
    except Exception as e: info.append('cur ' + str(e)[:60])
    mgr.DynamicCrossSectionModeOn = True
info.append('count=%d on=%s doc=%s' % (mgr.DynamicCrossSectionsCount, mgr.DynamicCrossSectionModeOn, doc7.Name))
result = '; '.join(info)
