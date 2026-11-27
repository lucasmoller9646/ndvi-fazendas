"""Cobertura do solo de referência (Impact Observatory / Esri 10 m, ano mais recente no Planetary Computer)
na mesma grade do mapa (MODO=pais ou detalhe). Saída: out/lulc_<i>_<j>.png (classe por pixel, moda).

Classes IO: 1 água, 2 árvores, 4 vegetação alagada, 5 cultivos, 7 área construída, 8 solo exposto,
9 neve, 10 nuvem, 11 pastagem/campo (rangeland). 0 = sem dado.
"""
import os, sys, json, time
from collections import defaultdict
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
from paraguai import W0, E0, N0, S0, RES, NT, TPX, TPXH, OUT, bbox_bloco, pc_get, STAC, PCTOKEN

COL = 'io-lulc-annual-v02'

def lulc(bbox):
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    T = from_bounds(*bbox, TPX, TPXH)
    its = pc_get(STAC, params={'collections': COL, 'bbox': ','.join(map(str, bbox)), 'limit': 200}).json()['features']
    por_ano = defaultdict(list)
    for it in its:
        por_ano[(it['properties'].get('start_datetime') or it['properties'].get('datetime'))[:4]].append(it)
    ano = max(por_ano)
    tok = pc_get(PCTOKEN.format(COL)).json()['token']
    out = np.zeros((TPXH, TPX), 'u1')
    for it in por_ano[ano]:
        href = it['assets']['data']['href']; href += ('&' if '?' in href else '?') + tok
        for k in range(4):
            try:
                with rasterio.open(href, OVERVIEW_LEVEL=0) as src, WarpedVRT(src, crs='EPSG:4326', transform=T, width=TPX, height=TPXH,
                                                                           resampling=Resampling.mode, src_nodata=0, nodata=0) as v:
                    d = v.read(1)
                break
            except Exception as e:
                print('  nova tentativa', e); time.sleep(10)
        else:
            continue
        m = (out == 0) & (d > 0)
        out[m] = d[m]
    return out, ano

def main():
    anos = set()
    for j in range(NT):
        for i in range(NT):
            bb = bbox_bloco(i, j)
            a, ano = lulc(bb); anos.add(ano)
            Image.fromarray(a).save(OUT / f'lulc_{i}_{j}.png', optimize=True)
            print('bloco', i, j, ano, np.bincount(a.ravel(), minlength=12).tolist(), flush=True)
    (OUT / 'lulc.json').write_text(json.dumps({'fonte': 'Impact Observatory / Esri 10 m annual LULC (io-lulc-annual-v02)', 'anos': sorted(anos)}))

if __name__ == '__main__':
    main()
