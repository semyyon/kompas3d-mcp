import json, array, sys
b = sys.argv[1]
m = json.load(open(b+'.json', encoding='utf-8'))
raw = open(b+'.bin','rb').read(); nv = m['nv']
P = array.array('f'); P.frombytes(raw[:nv*12])
for r in m['parts']:
    if r.get('sub'):
        mm = r['m']; print('SUB %-40s o=(%.1f,%.1f,%.1f) rot=(%.3f,%.3f)' % (r['name'][:40], mm[12], mm[13], mm[14], mm[0], mm[1])); continue
    v0, vn = r['v']
    xs = P[3*v0::3][:vn]; ys = P[3*v0+1::3][:vn]; zs = P[3*v0+2::3][:vn]
    bb = (min(xs),max(xs),min(ys),max(ys),min(zs),max(zs)) if vn else (0,)*6
    print('%-42s f=%4d t=%5d bad=%d  x[%.1f,%.1f] y[%.1f,%.1f] z[%.1f,%.1f]' % ((r['name'][:42], r['faces'], r['tris'], r['bad']) + bb))
