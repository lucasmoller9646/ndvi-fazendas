"""Relevo das fazendas: modelo de elevação (Copernicus DEM GLO-30) recortado no retângulo de cada fazenda,
curvas de nível em GeoJSON e o próprio modelo em PNG para a vista 3D da página.

Fonte: Copernicus DEM GLO-30 (30 m, ESA/Airbus), lido do Microsoft Planetary Computer — público, sem conta.
Saída em docs/relevo/:
  <fazenda>.json          metadados (retângulo, grade, altitude mínima/máxima, equidistância das curvas)
  <fazenda>_dem.png       altitude codificada em RGB: (R*256 + G) / 10 + zmin  (decímetros acima do mínimo)
  <fazenda>_curvas.geojson  curvas de nível (propriedades: z em metros, mestra = true a cada 5 curvas)
O relevo não muda: cada fazenda só é processada de novo se o arquivo não existir, se o retângulo mudar
ou com RELEVO_FORCE=1. Teste sem internet: FAKE=1 python ndvi/relevo.py
"""
import os, json, math, time
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'config' / 'fazendas.json').read_text(encoding='utf-8'))
DOCS = ROOT / 'docs'; OUT = DOCS / 'relevo'
FAKE = os.environ.get('FAKE') == '1'
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
TOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/{}'
RES_M = 30.0
os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '5'); os.environ.setdefault('GDAL_HTTP_RETRY_DELAY', '3')

def _get(url, **kw):
    import requests
    for k in range(5):
        try:
            r = requests.get(url, timeout=120, **kw)
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(8 * (k + 1)); continue
            r.raise_for_status(); return r
        except requests.RequestException:
            time.sleep(8 * (k + 1))
    raise RuntimeError(f'sem resposta de {url}')

def dem(bbox, W, H):
    """altitude (m, float32) na grade W x H do retângulo bbox (EPSG:4326); nan onde não há dado"""
    if FAKE:
        yy, xx = np.mgrid[0:H, 0:W]
        return (360 + 18 * np.sin(xx / 70) * np.cos(yy / 55) + 0.04 * xx - 0.03 * yy).astype('float32'), 'sintético'
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    T = from_bounds(*bbox, W, H)
    for col in ('cop-dem-glo-30', 'nasadem'):
        its = _get(STAC, params={'collections': col, 'bbox': ','.join(map(str, bbox)), 'limit': 20}).json().get('features', [])
        if not its: continue
        tok = _get(TOKEN.format(col)).json()['token']
        z = np.full((H, W), np.nan, 'float32')
        for it in its:
            a = it['assets'].get('data') or it['assets'].get('elevation')
            href = a['href'] + ('&' if '?' in a['href'] else '?') + tok
            with rasterio.open(href) as src, WarpedVRT(src, crs='EPSG:4326', transform=T, width=W, height=H,
                                                      resampling=Resampling.bilinear, src_nodata=src.nodata, nodata=np.nan) as v:
                d = v.read(1).astype('float32')
            take = np.isnan(z) & np.isfinite(d) & (d > -100)
            z[take] = d[take]
        if np.isfinite(z).mean() > 0.9:
            return z, {'cop-dem-glo-30': 'Copernicus DEM GLO-30', 'nasadem': 'NASADEM'}[col]
    raise RuntimeError('modelo de elevação indisponível para o retângulo')

def suaviza(z, s=1.3):
    """gaussiana separável (sem scipy) — tira o serrilhado de 30 m antes de traçar as curvas"""
    r = int(3 * s); k = np.exp(-0.5 * (np.arange(-r, r + 1) / s) ** 2); k /= k.sum()
    p = np.pad(z, r, mode='edge')
    a = sum(k[i] * p[:, i:i + z.shape[1]] for i in range(2 * r + 1))
    return sum(k[i] * a[i:i + z.shape[0], :] for i in range(2 * r + 1))

def equidistancia(rng):
    for e in (1, 2, 5, 10, 20, 25, 50, 100):
        if rng / e <= 45: return e
    return 200

def processa(key):
    dj = DOCS / 'data' / f'{key}.json'
    if not dj.exists(): print(key, 'sem docs/data ainda'); return
    bbox = json.loads(dj.read_text(encoding='utf-8'))['bbox']
    meta_f = OUT / f'{key}.json'
    if meta_f.exists() and os.environ.get('RELEVO_FORCE') != '1':
        m = json.loads(meta_f.read_text(encoding='utf-8'))
        if m.get('bbox') == bbox and (OUT / f'{key}_dem.png').exists(): print(key, 'relevo já existe'); return
    lat = (bbox[1] + bbox[3]) / 2
    W = max(8, int(round((bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / RES_M)))
    H = max(8, int(round((bbox[3] - bbox[1]) * 110570 / RES_M)))
    z, fonte = dem(bbox, W, H)
    if np.isnan(z).any():                                   # buracos pequenos: preenche com a média
        m = np.nanmean(z); z = np.where(np.isnan(z), m, z)
    zmin, zmax = float(np.floor(z.min())), float(np.ceil(z.max()))
    # PNG: decímetros acima do mínimo em R (alto) e G (baixo)
    dm = np.clip(np.round((z - zmin) * 10), 0, 65535).astype('uint16')
    rgb = np.zeros((H, W, 3), 'uint8'); rgb[..., 0] = dm >> 8; rgb[..., 1] = dm & 255
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb, 'RGB').save(OUT / f'{key}_dem.png', optimize=True)
    # curvas de nível
    import contourpy
    from shapely.geometry import LineString
    zs = suaviza(z)
    e = equidistancia(zmax - zmin)
    xs = bbox[0] + (np.arange(W) + 0.5) * (bbox[2] - bbox[0]) / W
    ys = bbox[3] - (np.arange(H) + 0.5) * (bbox[3] - bbox[1]) / H
    gen = contourpy.contour_generator(xs, ys, zs, line_type='Separate')
    feats = []
    for lv in np.arange(math.ceil(zs.min() / e) * e, zs.max() + 1e-6, e):
        for ln in gen.lines(float(lv)):
            if len(ln) < 4: continue
            g = LineString(ln).simplify(0.00004)
            if g.length < 0.0012: continue                     # some com anéis de menos de ~130 m
            c = [[round(x, 6), round(y, 6)] for x, y in g.coords]
            feats.append({'type': 'Feature', 'properties': {'z': int(lv) if float(lv).is_integer() else float(lv),
                          'mestra': bool(round(lv / e) % 5 == 0)}, 'geometry': {'type': 'LineString', 'coordinates': c}})
    (OUT / f'{key}_curvas.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}, separators=(',', ':')), encoding='utf-8')
    meta = {'bbox': bbox, 'W': W, 'H': H, 'zmin': zmin, 'zmax': zmax, 'equidistancia': e, 'mestra': 5 * e,
            'resolucao_m': RES_M, 'fonte': fonte, 'dem': f'relevo/{key}_dem.png', 'curvas': f'relevo/{key}_curvas.geojson'}
    meta_f.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    print(f'{key}: {W}x{H} px, {zmin:.0f}–{zmax:.0f} m, curvas de {e} m ({len(feats)} linhas), fonte {fonte}')

if __name__ == '__main__':
    so = os.environ.get('FAZENDAS')
    chaves = [k.strip() for k in so.split(',')] if so else list(CFG)
    falhas = []
    for k in chaves:
        if k not in CFG: continue
        try: processa(k)
        except Exception as ex:
            print(f'{k}: ERRO no relevo — {ex}'); falhas.append(k)
    if falhas: print('relevo com erro em:', ', '.join(falhas))
