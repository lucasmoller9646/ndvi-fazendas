"""Dados do mapa de parede do Paraguai (A0): mosaico Sentinel-2 da época seca e relevo Copernicus GLO-90.

Grade: EPSG:4326, 0,001° por pixel (~100 m, a resolução máxima de impressão em A0 a 1:1.100.000),
retângulo lon -63,2…-53,9 / lat -28,2…-18,9, dividido em 4 x 4 blocos de 2325 x 2325 px.

Saída em out/ (vai para o ramo "mapa-dados", regravado sem histórico):
  s2_rgb_<i>_<j>.png   R,G,B  — 255*sqrt(refl/0.4)
  s2_ext_<i>_<j>.png   NIR, SWIR (255*sqrt(refl/0.6)), qualidade (255 limpo, 128 nuvem/sombra, 0 sem dado)
  dem_<i>_<j>.png      16 bits: (altitude_m + 100) * 10
  grade.json           bbox, resolução, blocos e datas usadas
i = coluna (oeste→leste), j = linha (norte→sul).

MODO=detalhe: só o recorte das fazendas (lon -57,25…-55,95 / lat -22,1…-23,65) a 0,0004° (~45 m), 2 x 2 blocos.

Credenciais do Sentinel Hub: CDSE_CLIENT_ID / CDSE_CLIENT_SECRET (as mesmas do NDVI).
O relevo vem do Microsoft Planetary Computer (público).
"""
import os, json, time
from pathlib import Path
import numpy as np
import requests
from PIL import Image

MODO = os.environ.get('MODO', 'pais')
if MODO == 'detalhe':      # recorte das fazendas para o mapa de detalhe (~45 m/px)
    W0, E0, N0, S0 = -57.25, -55.95, -22.1, -23.65
    RES = 0.0004
    NT = 2
    TPXH = int(round((N0 - S0) / RES / NT))
else:
    W0, E0, N0, S0 = -63.2, -53.9, -18.9, -28.2
    RES = 0.001
    NT = 4
    TPXH = None
TPX = int(round((E0 - W0) / RES / NT))          # 2325 no modo país
TPXH = TPXH or TPX
OUT = Path(os.environ.get('OUT', 'out')); OUT.mkdir(exist_ok=True)
SO_DEM = os.environ.get('SO_DEM') == '1'

TOKEN_URL = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
PROCESS_URL = 'https://sh.dataspace.copernicus.eu/process/v1'
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
PCTOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/{}'

# época seca (céu limpo); a mais recente primeiro, a anterior só preenche buracos
PERIODOS = [('2026-06-15', '2026-09-30'), ('2025-06-15', '2025-09-30'), ('2024-06-01', '2024-10-15')]

EVALSCRIPT = """//VERSION=3
function setup(){return {input:[{bands:["B02","B03","B04","B08","B11","SCL","dataMask"]}],
  output:{bands:6, sampleType:"UINT8"}};}
function e(v,m){return 255*Math.sqrt(Math.max(0,Math.min(1,v/m)));}
function evaluatePixel(s){
  var q = 0;
  if (s.dataMask==1){ q = (s.SCL==4||s.SCL==5||s.SCL==6||s.SCL==7||s.SCL==11) ? 255 : 128; }
  return [e(s.B04,0.4), e(s.B03,0.4), e(s.B02,0.4), e(s.B08,0.6), e(s.B11,0.6), q];
}"""

_tok = {'v': None, 't': 0}
def auth():
    if time.time() - _tok['t'] > 400:
        r = requests.post(TOKEN_URL, data={'grant_type': 'client_credentials',
                                           'client_id': os.environ['CDSE_CLIENT_ID'],
                                           'client_secret': os.environ['CDSE_CLIENT_SECRET']}, timeout=60)
        r.raise_for_status(); _tok.update(v=r.json()['access_token'], t=time.time())
    return {'Authorization': 'Bearer ' + _tok['v']}

def bbox_bloco(i, j):
    w = W0 + i * TPX * RES; n = N0 - j * TPXH * RES
    return [round(w, 6), round(n - TPXH * RES, 6), round(w + TPX * RES, 6), round(n, 6)]

def s2(bbox, d0, d1):
    import rasterio
    body = {'input': {'bounds': {'bbox': bbox, 'properties': {'crs': 'http://www.opengis.net/def/crs/EPSG/0/4326'}},
                      'data': [{'type': 'sentinel-2-l2a',
                                'dataFilter': {'timeRange': {'from': f'{d0}T00:00:00Z', 'to': f'{d1}T23:59:59Z'},
                                               'maxCloudCoverage': 30, 'mosaickingOrder': 'leastCC'}}]},
            'output': {'width': TPX, 'height': TPXH,
                       'responses': [{'identifier': 'default', 'format': {'type': 'image/tiff'}}]},
            'evalscript': EVALSCRIPT}
    for k in range(8):
        try:
            r = requests.post(PROCESS_URL, json=body, headers={**auth(), 'Accept': 'image/tiff'}, timeout=600)
        except Exception as e:
            print('  conexão caiu, nova tentativa:', e); time.sleep(20 * (k + 1)); continue
        if r.status_code in (429, 500, 502, 503, 504):
            print('  status', r.status_code, '— aguardando'); time.sleep(min(30 * (k + 1), 180)); continue
        break
    r.raise_for_status()
    time.sleep(3)
    with rasterio.MemoryFile(r.content) as mf, mf.open() as ds:
        return ds.read()                       # (6, H, W) uint8

def pc_get(url, **kw):
    for k in range(6):
        try:
            r = requests.get(url, timeout=120, **kw)
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(10 * (k + 1)); continue
            r.raise_for_status(); return r
        except requests.RequestException:
            time.sleep(10 * (k + 1))
    raise RuntimeError('sem resposta de ' + url)

def dem(bbox):
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    T = from_bounds(*bbox, TPX, TPXH)
    z = np.full((TPXH, TPX), np.nan, 'float32')
    its = pc_get(STAC, params={'collections': 'cop-dem-glo-90', 'bbox': ','.join(map(str, bbox)), 'limit': 100}).json()['features']
    tok = pc_get(PCTOKEN.format('cop-dem-glo-90')).json()['token']
    for it in its:
        href = it['assets']['data']['href']; href += ('&' if '?' in href else '?') + tok
        for k in range(4):
            try:
                with rasterio.open(href) as src, WarpedVRT(src, crs='EPSG:4326', transform=T, width=TPX, height=TPXH,
                                                          resampling=Resampling.bilinear, src_nodata=src.nodata,
                                                          nodata=np.nan) as v:
                    d = v.read(1).astype('float32')
                break
            except Exception as e:
                print('  dem: nova tentativa', e); time.sleep(10)
        else:
            continue
        ok = np.isnan(z) & np.isfinite(d) & (d > -100)
        z[ok] = d[ok]
    return z

def main():
    meta = {'modo': MODO, 'bbox': [W0, S0, E0, N0], 'res_deg': RES, 'blocos': NT, 'px_bloco': [TPX, TPXH],
            'periodos': PERIODOS, 'fonte_s2': 'Copernicus Sentinel-2 L2A (ESA)', 'fonte_dem': 'Copernicus DEM GLO-90 (ESA/Airbus)',
            'datas': {}}
    os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
    for j in range(NT):
        for i in range(NT):
            bb = bbox_bloco(i, j); tag = f'{i}_{j}'
            print('bloco', tag, bb, flush=True)
            z = dem(bb)
            zz = np.where(np.isfinite(z), np.clip((z + 100) * 10, 1, 65535), 0).astype('uint16')
            Image.fromarray(zz).save(OUT / f'dem_{tag}.png', optimize=True)
            print('  dem ok', np.nanmin(z), np.nanmax(z), flush=True)
            if SO_DEM: continue
            comp = None
            for d0, d1 in PERIODOS:
                a = s2(bb, d0, d1)
                if comp is None:
                    comp = a.copy()
                else:
                    pior = comp[5] < 255
                    melhor = a[5] > comp[5]
                    t = pior & melhor
                    comp[:, t] = a[:, t]
                falta = (comp[5] < 255).mean()
                print(f'  s2 {d0}..{d1}: ainda sem dado limpo {falta:.1%}', flush=True)
                if falta < 0.002: break
            Image.fromarray(np.moveaxis(comp[:3], 0, -1)).save(OUT / f's2_rgb_{tag}.png', optimize=True)
            Image.fromarray(np.moveaxis(comp[3:6], 0, -1)).save(OUT / f's2_ext_{tag}.png', optimize=True)
    (OUT / 'grade.json').write_text(json.dumps(meta, indent=1))

if __name__ == '__main__':
    main()
