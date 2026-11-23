"""Landsat 8 e 9 — Collection 2 Level-2 (reflectância de superfície, 30 m), via Microsoft Planetary Computer.

Gratuito e sem conta: o catálogo STAC é público e os arquivos (COG no Azure) são lidos com um token SAS anônimo.
Não consome a cota do Copernicus. O NDVI é reamostrado para a mesma grade de 10 m do Sentinel-2.
Máscara pelo QA_PIXEL: fica só o pixel marcado como "limpo" ou "água", sem nuvem, nuvem dilatada, cirrus,
sombra ou neve.
"""
import os, math, time, datetime as dt
import numpy as np

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
TOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2'
PLAT = {'landsat-8': 'L8', 'landsat-9': 'L9'}
FAKE = os.environ.get('FAKE') == '1'
os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
os.environ.setdefault('GDAL_HTTP_MERGE_CONSECUTIVE_RANGES', 'YES')
os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '5')
os.environ.setdefault('GDAL_HTTP_RETRY_DELAY', '3')
os.environ.setdefault('CPL_VSIL_CURL_ALLOWED_EXTENSIONS', '.tif,.TIF')

_tok = {'v': None, 't': 0}
def _token():
    import requests
    if time.time() - _tok['t'] > 1800:
        r = requests.get(TOKEN, timeout=60); r.raise_for_status(); _tok.update(v=r.json()['token'], t=time.time())
    return _tok['v']

def _sign(href):
    return href + ('&' if '?' in href else '?') + _token() if href.startswith('http') else href

def catalog(bbox, d0, d1):
    """{dia: [itens]} das cenas Landsat 8/9 que cobrem o bbox"""
    if FAKE:
        out, d = {}, d1 - dt.timedelta(days=2)
        while d >= d0:
            out[d.isoformat()] = [{'fake': True, 'platform': 'landsat-9' if d.day % 2 else 'landsat-8'}]; d -= dt.timedelta(days=8)
        return out
    import requests
    params = {'collections': 'landsat-c2-l2', 'bbox': ','.join(map(str, bbox)),
              'datetime': f'{d0.isoformat()}T00:00:00Z/{d1.isoformat()}T23:59:59Z', 'limit': 100}
    url = STAC; out = {}
    while url:
        r = None
        for k in range(5):
            try:
                r = requests.get(url, params=params, timeout=120)
                if r.status_code in (429, 500, 502, 503, 504): time.sleep(10 * (k + 1)); continue
                r.raise_for_status(); break
            except requests.RequestException:
                time.sleep(10 * (k + 1))
        else:
            raise RuntimeError(f'catálogo Landsat indisponível ({getattr(r, "status_code", "sem resposta")})')
        j = r.json()
        for it in j.get('features', []):
            pl = it['properties'].get('platform')
            if pl not in PLAT: continue
            day = it['properties']['datetime'][:10]
            it['platform'] = pl
            out.setdefault(day, []).append(it)
        nxt = [l for l in j.get('links', []) if l.get('rel') == 'next']
        url = nxt[0]['href'] if nxt else None; params = None
    return out

def fetch(items, T, W, H):
    """NDVI (-9 inválido), máscara de dado (1/0) e satélite ('L8'/'L9') na grade da fazenda"""
    sat = PLAT.get(items[0].get('platform'), 'L')
    if FAKE:
        rng = np.random.default_rng(len(items) + W)
        yy, xx = np.mgrid[0:H, 0:W]
        nd = 0.55 + 0.12 * np.sin(xx / 90) * np.cos(yy / 70) + rng.normal(0, 0.03, (H, W))
        cm = (np.sin(xx / 150 + rng.random() * 6) + np.cos(yy / 120)) > 1.6
        return np.where(cm, -9, np.clip(nd, -0.2, 0.95)).astype('float32'), np.ones((H, W), 'float32'), sat
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    nd = np.full((H, W), -9, 'float32'); dm = np.zeros((H, W), 'float32')
    with rasterio.Env():
        for it in items:
            A = it['assets']
            def read(key, rs):
                with rasterio.open(_sign(A[key]['href'])) as src, \
                        WarpedVRT(src, crs='EPSG:4326', transform=T, width=W, height=H, resampling=rs, nodata=0) as v:
                    return v.read(1)
            qa = read('qa_pixel', Resampling.nearest).astype('uint16')
            red = read('red', Resampling.bilinear).astype('float32')
            nir = read('nir08', Resampling.bilinear).astype('float32')
            has = (qa != 0) & (qa != 1) & (red > 0) & (nir > 0)
            ok = has & ((qa & 0b111110) == 0) & (((qa >> 6) & 1) | ((qa >> 7) & 1)).astype(bool)
            r = red * 0.0000275 - 0.2; n = nir * 0.0000275 - 0.2
            v = np.where(ok & ((n + r) > 0), (n - r) / np.maximum(n + r, 1e-6), -9).astype('float32')
            take = (nd < -1.5) & (v > -1.5)          # mosaico: primeira cena válida em cada pixel
            nd[take] = np.clip(v[take], -1, 1); dm[has] = 1
    return nd, dm, sat
