"""Avanço das áreas de trabalho (abertura / preparo de solo) a partir do NDVI.

Regras, pixel a pixel (10 m), só em imagens com a fazenda quase sem nuvem (>= min_limpo):
  * "trabalhado": o pixel apareceu como solo exposto (0 <= NDVI < limiar_solo) pela primeira vez
    a partir da data `inicio`, e NÃO estava exposto antes dela (estradas, currais e aguadas
    que já eram solo/água ficam de fora);
  * "solo exposto agora": NDVI < limiar_solo na data;
  * "verde de novo": já trabalhado e com NDVI >= limiar_verde na data (forrageira emergindo).
O estado (data em que cada pixel virou solo) fica em docs/img/_estado/<fazenda>_obras.npz.
"""
import json, datetime as dt
import numpy as np
from rasterio.features import rasterize
from shapely.geometry import shape

def _ord(day): return dt.date.fromisoformat(day).toordinal()

class Obras:
    def __init__(self, root, docs, key, cfg, T, H, W):
        self.cfg = cfg; self.key = key
        gj = json.loads((root / cfg['geojson']).read_text(encoding='utf-8'))
        self.gj = gj
        self.units = [f['properties'] for f in gj['features']]
        self.ids = rasterize([(shape(f['geometry']), i + 1) for i, f in enumerate(gj['features'])],
                             out_shape=(H, W), transform=T, fill=0, dtype='int32')
        self.n = len(self.units)
        self.grupo = np.array([0] + [sorted({u['area'] for u in self.units}).index(u['area']) + 1 for u in self.units])
        self.areas = sorted({u['area'] for u in self.units})
        self.f = docs / 'img' / '_estado' / f'{key}_obras.npz'
        self.f.parent.mkdir(parents=True, exist_ok=True)
        if self.f.exists():
            z = np.load(self.f); self.first = z['first']; self.base = z['base']
        else:
            self.first = np.zeros((H, W), 'int32'); self.base = np.zeros((H, W), bool)
        self.inicio = _ord(cfg['inicio'])
        self.ls = cfg.get('limiar_solo', 0.35); self.lv = cfg.get('limiar_verde', 0.55)
        self.min_limpo = cfg.get('min_limpo', 0.9)

    def update(self, day, nd, limpo, D):
        """nd: NDVI com -9 onde inválido (nuvem/sem dado)"""
        valid = (nd > -1.5) & (self.ids > 0)
        bare = valid & (nd >= 0) & (nd < self.ls)
        o = _ord(day)
        if limpo >= self.min_limpo:
            if o < self.inicio:
                if o >= self.inicio - 180: self.base |= bare
            else:   # guarda a data MAIS ANTIGA em que o pixel apareceu exposto (vale em qualquer ordem de processamento)
                new = bare & ((self.first == 0) | (self.first > o))
                self.first[new] = o
        if o < self.inicio - 60 or limpo < 0.6:
            return
        trab = (self.first > 0) & (self.first <= o) & ~self.base
        green = trab & valid & (nd >= self.lv)
        S = D.setdefault('obras', {'inicio': self.cfg['inicio'], 'unidades': self.units, 'areas': self.areas, 'serie': {}})
        S['unidades'] = self.units; S['areas'] = self.areas; S['inicio'] = self.cfg['inicio']
        S['limiares'] = {'solo': self.ls, 'verde': self.lv}
        def put(key, sel_tot):
            tot = sel_tot.sum()
            if tot == 0: return
            v = (sel_tot & valid).sum()
            row = [day, round(100 * (sel_tot & trab).sum() / tot, 1),
                   round(100 * (sel_tot & bare).sum() / max(v, 1), 1) if v / tot >= 0.6 else None,
                   round(100 * (sel_tot & green).sum() / tot, 1), round(v / tot, 2)]
            ser = S['serie'].setdefault(key, [])
            ser[:] = [r for r in ser if r[0] != day] + [row]; ser.sort(key=lambda r: r[0])
        for i, u in enumerate(self.units, start=1):
            put(u['id'], self.ids == i)
        for gi, a in enumerate(self.areas, start=1):
            put(a, self.grupo[self.ids] == gi)
        put('_total', self.ids > 0)

    def recalcula(self, D):
        """refaz a coluna 'trabalhado' de todas as datas com o estado final (uma imagem processada depois
        pode ter revelado solo exposto numa data anterior)"""
        S = D.get('obras')
        if not S: return
        ok = (self.first > 0) & ~self.base
        dias = sorted({r[0] for ser in S['serie'].values() for r in ser})
        grp = self.grupo[self.ids]
        tot_u = np.bincount(self.ids.ravel(), minlength=self.n + 1)
        tot_g = np.bincount(grp.ravel(), minlength=len(self.areas) + 1)
        tot_t = (self.ids > 0).sum()
        for d in dias:
            t = ok & (self.first <= _ord(d)) & (self.ids > 0)
            cu = np.bincount(self.ids[t], minlength=self.n + 1); cg = np.bincount(grp[t], minlength=len(self.areas) + 1)
            val = {u['id']: 100 * cu[i] / tot_u[i] for i, u in enumerate(self.units, start=1) if tot_u[i]}
            val.update({a: 100 * cg[i] / tot_g[i] for i, a in enumerate(self.areas, start=1) if tot_g[i]})
            val['_total'] = 100 * t.sum() / tot_t
            for k, ser in S['serie'].items():
                for r in ser:
                    if r[0] == d and k in val: r[1] = round(float(val[k]), 1)

    def save(self):
        np.savez_compressed(self.f, first=self.first, base=self.base)
