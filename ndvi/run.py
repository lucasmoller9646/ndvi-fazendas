"""NDVI automático das fazendas — Sentinel-2 L2A via Copernicus Data Space (Sentinel Hub).

A cada execução:
  1. procura no catálogo as passagens do Sentinel-2 sobre cada fazenda que ainda não foram processadas;
  2. baixa, para cada data, o NDVI (10 m) já com a máscara de nuvem/sombra (SCL) aplicada;
  3. calcula NDVI médio, P10, P90 e % de área limpa de cada pasto;
  4. salva a imagem colorida (para o mapa) quando a fazenda está razoavelmente limpa de nuvens;
  5. reescreve docs/data/<fazenda>.json, que alimenta a página do GitHub Pages.

Credenciais: variáveis de ambiente CDSE_CLIENT_ID e CDSE_CLIENT_SECRET (segredos do repositório).
Teste sem internet: FAKE=1 python ndvi/run.py  (gera imagens sintéticas).
"""
import os, io, json, math, datetime as dt, time
from pathlib import Path
import numpy as np
from PIL import Image
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'config' / 'fazendas.json').read_text(encoding='utf-8'))
DOCS = ROOT / 'docs'
TOKEN_URL = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token'
CATALOG_URL = 'https://sh.dataspace.copernicus.eu/catalog/v1/search'
PROCESS_URL = 'https://sh.dataspace.copernicus.eu/process/v1'
FAKE = os.environ.get('FAKE') == '1'

# NDVI só onde o pixel é vegetação, solo, água ou "sem classe" (SCL 4,5,6,7); nuvem, sombra, cirrus e
# pixels saturados/escuros ficam -9. Banda 2 = 1 quando há dado do satélite naquele pixel.
EVALSCRIPT = """//VERSION=3
function setup(){return{input:[{bands:["B04","B08","SCL","dataMask"]}],output:{bands:2,sampleType:"FLOAT32"}}}
function evaluatePixel(s){
  var ok = s.dataMask==1 && (s.SCL==4||s.SCL==5||s.SCL==6||s.SCL==7);
  var d = s.B08+s.B04;
  var n = (ok && d>0) ? (s.B08-s.B04)/d : -9;
  return [n, s.dataMask];
}"""

# ---------------------------------------------------------------- acesso ao Copernicus
_tok = {'v': None, 't': 0}
def session():
    import requests
    s = requests.Session()
    if time.time() - _tok['t'] > 500:
        r = requests.post(TOKEN_URL, data={'grant_type': 'client_credentials',
                                           'client_id': os.environ['CDSE_CLIENT_ID'],
                                           'client_secret': os.environ['CDSE_CLIENT_SECRET']}, timeout=60)
        r.raise_for_status(); _tok.update(v=r.json()['access_token'], t=time.time())
    s.headers['Authorization'] = 'Bearer ' + _tok['v']
    return s

def catalog_dates(bbox, d0, d1):
    """datas (AAAA-MM-DD, horário local do Paraguai ~ UTC) com cena Sentinel-2 L2A sobre o bbox"""
    if FAKE:
        out, d = [], d1
        while d >= d0:
            out.append(d.isoformat()); d -= dt.timedelta(days=5)
        return sorted(out)
    body = {'bbox': bbox, 'datetime': f'{d0.isoformat()}T00:00:00Z/{d1.isoformat()}T23:59:59Z',
            'collections': ['sentinel-2-l2a'], 'limit': 100,
            'fields': {'include': ['properties.datetime', 'properties.eo:cloud_cover'], 'exclude': []}}
    dates = {}
    while True:
        r = session().post(CATALOG_URL, json=body, timeout=120); r.raise_for_status(); j = r.json()
        for f in j.get('features', []):
            day = f['properties']['datetime'][:10]
            cc = f['properties'].get('eo:cloud_cover', 0)
            dates[day] = min(cc, dates.get(day, 999))
        nxt = j.get('context', {}).get('next')
        if not nxt: break
        body['next'] = nxt
    return sorted(dates)

def fetch_ndvi(bbox, W, H, day):
    """GeoTIFF FLOAT32 2 bandas (ndvi|-9, dataMask) para um dia"""
    if FAKE:
        rng = np.random.default_rng(abs(hash(day)) % 2**32)
        yy, xx = np.mgrid[0:H, 0:W]
        doy = dt.date.fromisoformat(day).timetuple().tm_yday
        base = 0.55 + 0.2 * math.sin(2 * math.pi * (doy - 30) / 365)
        nd = base + 0.12 * np.sin(xx / 90) * np.cos(yy / 70) + rng.normal(0, 0.03, (H, W))
        cloud = rng.random() * 0.6
        cm = (np.sin(xx / 150 + rng.random() * 6) + np.cos(yy / 120 + rng.random() * 6)) > 2 - 4 * cloud
        nd = np.where(cm, -9, np.clip(nd, -0.2, 0.95)).astype('float32')
        return nd, np.ones_like(nd)
    body = {
        'input': {'bounds': {'bbox': bbox, 'properties': {'crs': 'http://www.opengis.net/def/crs/EPSG/0/4326'}},
                  'data': [{'type': 'sentinel-2-l2a',
                            'dataFilter': {'timeRange': {'from': f'{day}T00:00:00Z', 'to': f'{day}T23:59:59Z'},
                                           'mosaickingOrder': 'leastCC'}}]},
        'output': {'width': W, 'height': H, 'responses': [{'identifier': 'default', 'format': {'type': 'image/tiff'}}]},
        'evalscript': EVALSCRIPT}
    for k in range(8):
        try:
            r = session().post(PROCESS_URL, json=body, headers={'Accept': 'image/tiff'}, timeout=300)
        except Exception as e:      # queda de conexão: tenta de novo
            print('  erro de conexão, nova tentativa:', e); time.sleep(15 * (k + 1)); continue
        if r.status_code in (429, 500, 502, 503, 504):   # limite por minuto do Sentinel Hub ou instabilidade
            time.sleep(min(20 * (k + 1), 120)); continue
        break
    r.raise_for_status()
    time.sleep(float(os.environ.get('PAUSA', '2')))   # respeita o limite de unidades por minuto
    with rasterio.MemoryFile(r.content) as mf, mf.open() as ds:
        a = ds.read().astype('float32')
    return a[0], a[1]

# ---------------------------------------------------------------- cores (paleta NDVI)
STOPS = [(-0.2, (120, 80, 50)), (0.0, (165, 120, 75)), (0.2, (215, 180, 110)), (0.35, (235, 225, 140)),
         (0.5, (170, 205, 110)), (0.65, (95, 170, 75)), (0.8, (35, 120, 50)), (0.95, (10, 70, 30))]
def colorize(nd, valid):
    v = np.clip(nd, STOPS[0][0], STOPS[-1][0]); out = np.zeros(nd.shape + (4,), 'uint8')
    xs = [s[0] for s in STOPS]
    for c in range(3):
        out[..., c] = np.interp(v, xs, [s[1][c] for s in STOPS]).astype('uint8')
    out[..., 3] = np.where(valid, 255, 0)
    return out

# ---------------------------------------------------------------- processamento por fazenda
def run_farm(key, f):
    pastos = json.loads((ROOT / f['pastos']).read_text(encoding='utf-8'))
    feats = pastos['features']
    xs = [c for ft in feats for ring in _rings(ft['geometry']) for c, _ in ring]
    ys = [c for ft in feats for ring in _rings(ft['geometry']) for _, c in ring]
    pad = 0.004
    bbox = [round(min(xs) - pad, 5), round(min(ys) - pad, 5), round(max(xs) + pad, 5), round(max(ys) + pad, 5)]
    lat = (bbox[1] + bbox[3]) / 2
    W = int(round((bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / 10))
    H = int(round((bbox[3] - bbox[1]) * 110570 / 10))
    T = from_bounds(*bbox, W, H)
    ids = rasterize([(shape(ft['geometry']), i + 1) for i, ft in enumerate(feats)], out_shape=(H, W), transform=T,
                    fill=0, dtype='int32', all_touched=False)
    inside = ids > 0
    datafile = DOCS / 'data' / f'{key}.json'
    D = json.loads(datafile.read_text(encoding='utf-8')) if datafile.exists() else {
        'fazenda': f['nome'], 'bbox': bbox, 'pastos_geojson': f'data/{key}_pastos.geojson', 'passagens': [], 'serie': {}}
    D['bbox'] = bbox
    feito = {p['data'] for p in D['passagens']}
    hoje = dt.date.today()
    d0 = dt.date.fromisoformat(f.get('inicio', '2025-11-01'))
    if D['passagens']:
        d0 = max(d0, hoje - dt.timedelta(days=int(os.environ.get('JANELA_DIAS', '20'))))
    datas = [d for d in catalog_dates(bbox, d0, hoje) if d not in feito]
    print(f'{key}: {len(datas)} passagem(ns) nova(s) entre {d0} e {hoje}')
    novas = 0
    for day in datas:
        nd, dm = fetch_ndvi(bbox, W, H, day)
        if nd.shape != (H, W):
            print('  tamanho inesperado', nd.shape); continue
        has = (dm > 0) & inside; valid = (nd > -1.5) & inside
        cob = has.sum() / inside.sum()
        limpo = valid.sum() / max(has.sum(), 1)
        reg = {'data': day, 'cobertura': round(float(cob), 3), 'limpo': round(float(limpo), 3), 'img': None}
        if cob < 0.5:
            print(f'  {day}: fora da faixa do satélite ({cob:.0%})'); D['passagens'].append(reg); continue
        # estatística por pasto
        flat_id = ids[valid]; flat_v = nd[valid]
        nval = np.bincount(flat_id, minlength=len(feats) + 1)
        ntot = np.bincount(ids[has], minlength=len(feats) + 1)
        soma = np.bincount(flat_id, weights=flat_v, minlength=len(feats) + 1)
        for i, ft in enumerate(feats, start=1):
            if ntot[i] == 0: continue
            pid = ft['properties']['id']
            fr = nval[i] / ntot[i]
            if fr < 0.6 or nval[i] < 20:   # pasto muito encoberto: não entra na série
                continue
            vv = flat_v[flat_id == i]
            D['serie'].setdefault(pid, []).append([day, round(float(soma[i] / nval[i]), 3),
                                                   round(float(np.percentile(vv, 10)), 3),
                                                   round(float(np.percentile(vv, 90)), 3), round(float(fr), 2)])
        if limpo >= f.get('min_limpo_mapa', 0.6):
            img = DOCS / 'img' / key / f'ndvi_{day}.png'; img.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(colorize(nd, nd > -1.5)).convert('RGBA').quantize(colors=96, method=Image.Quantize.FASTOCTREE).save(img, optimize=True)
            reg['img'] = f'img/{key}/ndvi_{day}.png'
        D['passagens'].append(reg); novas += 1
        print(f'  {day}: cobertura {cob:.0%}, limpo {limpo:.0%}' + (' → mapa salvo' if reg['img'] else ''))
        if novas % 10 == 0: save(D, datafile)
    D['passagens'].sort(key=lambda p: p['data'])
    for k in D['serie']:
        D['serie'][k] = sorted({r[0]: r for r in D['serie'][k]}.values())
    # mantém no repositório só os últimos N mapas (a série por pasto fica completa)
    keep = f.get('mapas_guardados', 40)
    com_img = [p for p in D['passagens'] if p['img']]
    for p in com_img[:-keep]:
        fp = DOCS / p['img']
        if fp.exists(): fp.unlink()
        p['img'] = None
    save(D, datafile)
    gj = DOCS / 'data' / f'{key}_pastos.geojson'
    gj.write_text(json.dumps(pastos, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    return novas

def save(D, datafile):
    D['atualizado'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    datafile.write_text(json.dumps(D, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

def _rings(g):
    if g['type'] == 'Polygon': return g['coordinates']
    return [r for p in g['coordinates'] for r in p]

if __name__ == '__main__':
    total = 0; falhas = []
    so = os.environ.get('FAZENDAS')   # opcional: rodar só algumas (separadas por vírgula)
    for key, f in CFG.items():
        if so and key not in so.split(','): continue
        try:
            total += run_farm(key, f)
        except Exception as e:
            import traceback; traceback.print_exc(); falhas.append(key)
    idx = {k: {'nome': v['nome'], 'arquivo': f'data/{k}.json'} for k, v in CFG.items() if (DOCS / 'data' / f'{k}.json').exists()}
    (DOCS / 'data' / 'fazendas.json').write_text(json.dumps(idx, ensure_ascii=False), encoding='utf-8')
    print('passagens novas processadas:', total)
    if falhas:
        print('FAZENDAS COM ERRO:', ', '.join(falhas)); raise SystemExit(1)
