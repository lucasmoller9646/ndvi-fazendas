"""Dados dos mapas temáticos pequenos do mapa de parede (ecorregiões, clima, água, inundação, solos, aquíferos, rede elétrica).

Cada fonte é independente: se uma falhar, as outras seguem e o erro fica no log.
Saída em out/ (ramo mapa-temas).
"""
import os, io, json, time, zipfile, traceback
from pathlib import Path
import numpy as np
import requests

OUT = Path(os.environ.get('OUT', 'out')); OUT.mkdir(exist_ok=True)
BB = (-64.0, -29.0, -53.0, -18.0)            # oeste, sul, leste, norte
os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')
os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '5'); os.environ.setdefault('GDAL_HTTP_RETRY_DELAY', '5')
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
PCTOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/{}'
LOG = {}

def get(url, **kw):
    for k in range(5):
        try:
            r = requests.get(url, timeout=600, **kw)
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(15 * (k + 1)); continue
            r.raise_for_status(); return r
        except requests.RequestException as e:
            print('  tentativa', k, e); time.sleep(15 * (k + 1))
    raise RuntimeError('sem resposta: ' + url)

def etapa(nome):
    def deco(fn):
        def run():
            t = time.time(); print('==', nome, flush=True)
            try:
                LOG[nome] = fn() or 'ok'
            except Exception as e:
                traceback.print_exc(); LOG[nome] = 'ERRO: ' + repr(e)[:300]
            print('  ->', LOG[nome], f'({time.time() - t:.0f}s)', flush=True)
        return run
    return deco

def warp(href, res, resampling, dtype, nodata=0, overview=None, src_crs=None):
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.transform import from_bounds
    W = int(round((BB[2] - BB[0]) / res)); H = int(round((BB[3] - BB[1]) / res))
    T = from_bounds(*BB, W, H)
    kw = {} if overview is None else {'OVERVIEW_LEVEL': overview}
    with rasterio.open(href, **kw) as src, WarpedVRT(src, crs='EPSG:4326', transform=T, width=W, height=H,
                                                    resampling=resampling, nodata=nodata) as v:
        return v.read(1).astype(dtype), T

def save_tif(name, arr, T, dtype):
    import rasterio
    with rasterio.open(OUT / name, 'w', driver='GTiff', width=arr.shape[1], height=arr.shape[0], count=1, dtype=dtype,
                       crs='EPSG:4326', transform=T, compress='deflate') as d:
        d.write(arr.astype(dtype), 1)

# ------------------------------------------------------------------ ecorregiões (RESOLVE 2017)
@etapa('ecorregioes')
def ecorregioes():
    import pyogrio
    from shapely import wkb
    from shapely.geometry import box, mapping
    z = OUT.parent / 'eco.zip'
    with open(z, 'wb') as f: f.write(get('https://storage.googleapis.com/teow2016/Ecoregions2017.zip').content)
    zf = zipfile.ZipFile(z); shp = [n for n in zf.namelist() if n.endswith('.shp')][0]
    meta, fids, geoms, fields = pyogrio.raw.read(f'/vsizip/{z}/{shp}', bbox=BB)
    names = list(meta['fields'])
    feats = []
    for i, g in enumerate(geoms):
        p = {n: (fields[k][i].item() if hasattr(fields[k][i], 'item') else fields[k][i]) for k, n in enumerate(names)}
        geom = wkb.loads(bytes(g)).intersection(box(*BB)).simplify(0.01)
        if geom.is_empty: continue
        feats.append({'type': 'Feature', 'properties': {k: p.get(k) for k in ('ECO_NAME', 'BIOME_NAME', 'ECO_ID')}, 'geometry': mapping(geom)})
    (OUT / 'ecoregions.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}))
    return f'{len(feats)} feições: ' + ', '.join(sorted({f["properties"]["ECO_NAME"] for f in feats}))

# ------------------------------------------------------------------ clima (WorldClim 2.1, 2,5')
@etapa('worldclim')
def worldclim():
    import rasterio
    from rasterio.windows import from_bounds as wfb
    res = {}
    for var in ('prec', 'tmin', 'tmax'):
        z = OUT.parent / f'wc_{var}.zip'
        with open(z, 'wb') as f: f.write(get(f'https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_{var}.zip').content)
        zf = zipfile.ZipFile(z)
        bands = []
        for m in range(1, 13):
            n = [x for x in zf.namelist() if x.endswith(f'_{m:02d}.tif')][0]
            with rasterio.open(f'/vsizip/{z}/{n}') as src:
                w = wfb(*BB, transform=src.transform)
                a = src.read(1, window=w).astype('f4'); a[a < -1e3] = np.nan
                T = src.window_transform(w)
            bands.append(a)
        res[var] = np.stack(bands)
        os.remove(z)
    np.savez_compressed(OUT / 'worldclim.npz', **res, transform=np.array(T)[:6])
    return 'ok ' + str(res['prec'].shape)

# ------------------------------------------------------------------ água superficial (JRC GSW occurrence 1984-2021)
@etapa('gsw')
def gsw():
    from rasterio.enums import Resampling
    its = get(STAC, params={'collections': 'jrc-gsw', 'bbox': ','.join(map(str, BB)), 'limit': 50}).json()['features']
    tok = get(PCTOKEN.format('jrc-gsw')).json()['token']
    acc = None
    for it in its:
        a = it['assets'].get('occurrence')
        if not a: continue
        href = a['href'] + ('&' if '?' in a['href'] else '?') + tok
        for ov in (3, 2, None):
            try:
                d, T = warp(href, 0.005, Resampling.average, 'f4', nodata=255, overview=ov); break
            except Exception as e:
                print('  overview', ov, e); d = None
        if d is None: continue
        d[d > 100] = 0
        acc = d if acc is None else np.maximum(acc, d)
    save_tif('gsw_occurrence.tif', np.nan_to_num(acc), T, 'uint8')
    return f'{len(its)} itens'

# ------------------------------------------------------------------ solos (SoilGrids WRB, classe mais provável)
@etapa('solos')
def solos():
    from rasterio.enums import Resampling
    base = 'https://files.isric.org/soilgrids/latest/data/wrb/'
    for leg in ('MostProbable.qml', 'MostProbable.sld', 'MostProbable.txt'):
        try:
            r = requests.get(base + leg, timeout=120)
            if r.ok: (OUT / ('wrb_' + leg)).write_text(r.text); print('  legenda', leg)
        except Exception as e: print('  legenda', leg, e)
    last = None
    for ov in (2, 1, 0, None):
        try:
            d, T = warp('/vsicurl/' + base + 'MostProbable.vrt', 0.01, Resampling.mode, 'u1', nodata=255, overview=ov)
            save_tif('soil_wrb.tif', d, T, 'uint8')
            return f'overview {ov}, classes ' + str(np.unique(d)[:40].tolist())
        except Exception as e:
            print('  overview', ov, e); last = e
    raise last

# ------------------------------------------------------------------ aquíferos
@etapa('aquiferos')
def aquiferos():
    got = []
    # IGRAC (Transboundary Aquifers of the World) — camada completa, recorte local
    from shapely.geometry import shape, box, mapping
    for n in ('geonode:tba_map_2025', 'geonode:a__2021_TBA_GGIS_utf8', 'tbamap2015:tbamap_2015ggis', 'igrac:twap_aquiferdata_aquifertype'):
        try:
            r = requests.get('https://ggis.un-igrac.org/geoserver/ows', params={'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
                             'typeNames': n, 'outputFormat': 'application/json', 'srsName': 'EPSG:4326'}, timeout=600)
            print('  IGRAC', n, r.status_code, len(r.content))
            if not (r.ok and r.text.lstrip().startswith('{')):
                print('   ', r.text[:300]); continue
            j = r.json(); bb = box(*BB); fs = []
            for f in j.get('features', []):
                try:
                    g = shape(f['geometry'])
                    if not g.intersects(bb):
                        from shapely import transform as stf
                        g2 = stf(g, lambda a: a[:, ::-1])
                        if g2.intersects(bb): g = g2
                        else: continue
                    f['geometry'] = mapping(g.intersection(bb).simplify(0.005)); fs.append(f)
                except Exception:
                    pass
            fn = 'aq_igrac_' + n.replace(':', '_') + '.geojson'
            (OUT / fn).write_text(json.dumps({'type': 'FeatureCollection', 'features': fs}))
            got.append(f'{fn}:{len(fs)} de {len(j.get("features", []))}')
            if fs: print('   props', fs[0]['properties'])
        except Exception as e:
            print('  IGRAC', n, e)
    # BGR WHYMAP (recursos de água subterrânea do mundo)
    for base in ('https://services.bgr.de/wfs/grundwasser/whymap_gwr/', 'https://services.bgr.de/wfs/grundwasser/whymap/'):
        try:
            cap = requests.get(base, params={'SERVICE': 'WFS', 'REQUEST': 'GetCapabilities'}, timeout=120).text
            import re
            names = sorted(set(re.findall(r'<(?:wfs:)?Name>([^<]+)</(?:wfs:)?Name>', cap)))
            print('  BGR camadas', base, names[:30])
            for n in names:
                r = requests.get(base, params={'SERVICE': 'WFS', 'VERSION': '2.0.0', 'REQUEST': 'GetFeature', 'TYPENAMES': n,
                                               'OUTPUTFORMAT': 'application/json', 'SRSNAME': 'EPSG:4326',
                                               'BBOX': f'{BB[1]},{BB[0]},{BB[3]},{BB[2]},urn:ogc:def:crs:EPSG::4326'}, timeout=300)
                if r.ok and r.text.lstrip().startswith('{'):
                    j = r.json()
                    if j.get('features'):
                        fn = 'aq_bgr_' + n.replace(':', '_') + '.geojson'
                        (OUT / fn).write_text(r.text); got.append(f'{fn}:{len(j["features"])}')
                else:
                    print('  BGR', n, r.status_code, r.text[:200])
        except Exception as e:
            print('  BGR', base, e)
    return ', '.join(got) or 'nada'

# ------------------------------------------------------------------ rede elétrica (OpenStreetMap)
@etapa('rede_eletrica')
def rede():
    q = f"""[out:json][timeout:300];
(
  way["power"="line"]({BB[1]},{BB[0]},{BB[3]},{BB[2]});
  way["power"="plant"]({BB[1]},{BB[0]},{BB[3]},{BB[2]});
  node["power"="plant"]({BB[1]},{BB[0]},{BB[3]},{BB[2]});
  way["power"="substation"]["voltage"]({BB[1]},{BB[0]},{BB[3]},{BB[2]});
  node["power"="substation"]["voltage"]({BB[1]},{BB[0]},{BB[3]},{BB[2]});
);
out tags geom;"""
    for url in ('https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'):
        try:
            r = None
            for k in range(4):
                r = requests.post(url, data={'data': q}, timeout=900,
                                  headers={'User-Agent': 'ndvi-fazendas-mapa/1.0 (github.com/lucasmoller9646/ndvi-fazendas)', 'Accept': '*/*'})
                if r.status_code in (429, 504): time.sleep(60 * (k + 1)); continue
                break
            if r.ok:
                (OUT / 'power_osm.json').write_text(r.text)
                return f'{url}: {len(r.json()["elements"])} elementos'
            print('  overpass', url, r.status_code)
        except Exception as e:
            print('  overpass', url, e)
    raise RuntimeError('overpass falhou')

if __name__ == '__main__':
    so = os.environ.get('ETAPAS', '')
    todas = {'ecorregioes': ecorregioes, 'worldclim': worldclim, 'gsw': gsw, 'solos': solos, 'aquiferos': aquiferos, 'rede_eletrica': rede}
    for k, fn in todas.items():
        if not so or k in so.split(','): fn()
    (OUT / 'temas_log.json').write_text(json.dumps(LOG, indent=1, ensure_ascii=False))
    print(json.dumps(LOG, indent=1, ensure_ascii=False))
