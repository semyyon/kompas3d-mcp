# Выгрузка триангуляции деталей ВЕРХНЕГО уровня активной 3D-сборки КОМПАС.
# Запуск из kompas_run_script:  PARAMS={'out': r'...\\name', 'only': [..] or None}; exec(open(r'...\\mesh_extract.py', encoding='utf-8').read())
# Пишет <out>.json (метаданные) и <out>.bin (float32 xyz, float32 нормали, uint32 индексы) — координаты в СК сборки.
from win32com.client import Dispatch, gencache
import pythoncom, json, array, time

m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
kompas = m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(
    m5.KompasObject.CLSID, pythoncom.IID_IDispatch))
doc5 = kompas.ActiveDocument3D()
out = PARAMS['out']
only = PARAMS.get('only')
skip_sub = PARAMS.get('skip_sub', True)

def m16(pl):
    r = pl.GetMatrix3D()
    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], (tuple, list)):
        r = r[1]
    return [float(v) for v in r]

def xf(m, x, y, z):   # строка-вектор, перенос в m[12..14]
    return (x*m[0] + y*m[4] + z*m[8] + m[12],
            x*m[1] + y*m[5] + z*m[9] + m[13],
            x*m[2] + y*m[6] + z*m[10] + m[14])

def xn(m, x, y, z):
    a, b, c = x*m[0] + y*m[4] + z*m[8], x*m[1] + y*m[5] + z*m[9], x*m[2] + y*m[6] + z*m[10]
    l = (a*a + b*b + c*c) ** 0.5 or 1.0
    return a/l, b/l, c/l

t0 = time.time()
meta = {'doc': doc5.fileName, 'parts': []}
P = array.array('f'); N = array.array('f'); I = array.array('I')
coll = doc5.PartCollection(True)
for i in range(coll.GetCount()):
    p = coll.GetByIndex(i)
    name = p.name
    if only and not any(s in name for s in only):
        continue
    fn = p.fileName or ''
    rec = {'i': i, 'name': name, 'file': fn, 'm': None, 'faces': 0, 'tris': 0, 'bad': 0}
    try:
        m = m16(p.GetPlacement())
    except Exception as e:
        m = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]; rec['merr'] = str(e)
    rec['m'] = m
    if skip_sub and fn.lower().endswith('.a3d'):
        rec['sub'] = True
        meta['parts'].append(rec); continue
    faces = p.EntityCollection(6)
    nf = faces.GetCount() if faces else 0
    rec['faces'] = nf
    p0, i0 = len(P)//3, len(I)
    for j in range(nf):
        try:
            d = faces.GetByIndex(j).GetDefinition()
            t = d.GetTessellation()
            r = t.GetFacetPoints()
            ok, pts, idx = (r if isinstance(r, tuple) and len(r) == 3 else (False, None, None))
            if not ok or not pts or not idx:
                rec['bad'] += 1; continue
            rn = t.GetFacetNormals()
            nrm = rn[1] if isinstance(rn, tuple) and len(rn) == 2 and rn[0] else None
            base = len(P)//3
            npts = len(pts)//3
            for k in range(npts):
                x, y, z = xf(m, pts[3*k], pts[3*k+1], pts[3*k+2])
                P.extend((x, y, z))
                if nrm and len(nrm) >= 3*npts:
                    N.extend(xn(m, nrm[3*k], nrm[3*k+1], nrm[3*k+2]))
                else:
                    N.extend((0.0, 0.0, 0.0))
            I.extend(base + int(v) for v in idx)
        except Exception as e:
            rec['bad'] += 1; rec['err'] = str(e)[:120]
    rec['v'] = [p0, len(P)//3 - p0]
    rec['ix'] = [i0, len(I) - i0]
    rec['tris'] = rec['ix'][1] // 3
    meta['parts'].append(rec)
    if time.time() - t0 > 230:
        meta['timeout_at'] = i; break

meta['nv'] = len(P)//3; meta['ni'] = len(I); meta['sec'] = round(time.time() - t0, 1)
with open(out + '.bin', 'wb') as fbin:
    P.tofile(fbin); N.tofile(fbin); I.tofile(fbin)
with open(out + '.json', 'w', encoding='utf-8') as fj:
    json.dump(meta, fj, ensure_ascii=False)
result = 'parts=%d nv=%d tris=%d sec=%s bad=%d' % (len(meta['parts']), meta['nv'], meta['ni']//3, meta['sec'],
                                                     sum(r.get('bad', 0) for r in meta['parts']))
