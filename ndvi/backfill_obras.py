"""Reconstrói o avanço das áreas de trabalho a partir dos mapas PNG já guardados (sem gastar cota).
Uso: python ndvi/backfill_obras.py la-esperanza [pasta_com_img]"""
import sys, json, math
from pathlib import Path
import numpy as np
from PIL import Image
from rasterio.transform import from_bounds
sys.path.insert(0, str(Path(__file__).parent))
from run import ROOT, DOCS, CFG, STOPS, save
from obras import Obras

key = sys.argv[1]; imgroot = Path(sys.argv[2]) if len(sys.argv) > 2 else DOCS
f = CFG[key]; datafile = DOCS / 'data' / f'{key}.json'
D = json.loads(datafile.read_text(encoding='utf-8'))
v = np.linspace(-0.2, 0.95, 231); xs = [a for a, _ in STOPS]
LUT = np.stack([np.interp(v, xs, [c[k] for _, c in STOPS]) for k in range(3)], 1)
com = [p for p in D['passagens'] if p['img']]
W, H = Image.open(imgroot / com[0]['img']).size
T = from_bounds(*D['bbox'], W, H)
est = DOCS / 'img' / '_estado' / f'{key}_obras.npz'
if est.exists(): est.unlink()
D.pop('obras', None)
ob = Obras(ROOT, DOCS, key, f['obras'], T, H, W)
for p in com:
    a = np.asarray(Image.open(imgroot / p['img']).convert('RGBA'))
    rgb = a[..., :3].reshape(-1, 3).astype(float)
    u, inv = np.unique(rgb, axis=0, return_inverse=True)
    nd = v[((u[:, None, :] - LUT[None]) ** 2).sum(-1).argmin(1)][inv.ravel()].reshape(H, W)
    nd = np.where(a[..., 3] > 0, nd, -9).astype('float32')
    ob.update(p['data'], nd, p['limpo'], D)
    print(p['data'], p['limpo'])
ob.save()
(DOCS / 'data' / f'{key}_obras.geojson').write_text(json.dumps(ob.gj, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
D['obras_geojson'] = f'data/{key}_obras.geojson'
save(D, datafile)
for k in ob.areas + ['_total']:
    print(k, D['obras']['serie'][k][-3:])
