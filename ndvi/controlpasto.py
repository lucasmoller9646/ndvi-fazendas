"""Lotação dos pastos a partir da API do ControlPasto (Integrasoftware).

API JSON-RPC 2.0 em http://app.controlpasto.com.br:8101/json (estrutura completa nessa mesma URL):
  LoginService.Login {UserName, Password}  -> devolve o "id" da sessão, usado em todas as chamadas seguintes
  ControlPastoService.LstFz_CargaInst {Data}              carga instantânea na data: fazenda > pasto > piquete > lote
  ControlPastoService.LstFz_HistLote  {DataIni, DataFim}  operações dos lotes no período
  LoginService.Logout {}
As credenciais vêm dos segredos do repositório CONTROLPASTO_USUARIO e CONTROLPASTO_CLAVE (opcional CONTROLPASTO_URL).
Sem elas o script não faz nada e a página continua com os últimos dados gravados.

Saída em docs/lotacao/ (o repositório é público: os dados vão CRIPTOGRAFADOS com a senha do site, AES-256-GCM,
chave = PBKDF2-SHA256(senha, 'ndvi-fazendas:lotacao', 150000) — a página decifra com a senha digitada na entrada):
  index.json        só metadados: data da leitura, fazendas com dados, erro se houver
  <fazenda>.json    {v, iv, ct}: lotes de hoje por pasto, série semanal de 12 meses, média de 12 meses e movimentações
  _cache.json       {v, iv, ct}: fotografias semanais já lidas (cada domingo), para não pedir tudo de novo a cada execução

Teste sem a API: FAKE=1 python ndvi/controlpasto.py   (gera dados de exemplo para todas as fazendas com mapa)
Diagnóstico: CP_DEBUG=1 imprime a estrutura (só nomes de campos e tipos) e grava cp_debug.bin, a resposta crua
criptografada com a chave pública config/controlpasto_debug_pub.pem (só quem tem a chave privada abre).
"""
import os, re, json, time, base64, hashlib, unicodedata, datetime as dt, random, io, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'config' / 'fazendas.json').read_text(encoding='utf-8'))
DOCS = ROOT / 'docs'; OUT = DOCS / 'lotacao'
URL = os.environ.get('CONTROLPASTO_URL') or 'http://app.controlpasto.com.br:8101/json'
USER = os.environ.get('CONTROLPASTO_USUARIO', '').strip(); PASS = os.environ.get('CONTROLPASTO_CLAVE', '')
SENHA = os.environ.get('SENHA_SITE') or '123321'
FAKE = os.environ.get('FAKE') == '1'; DEBUG = os.environ.get('CP_DEBUG') == '1'
UA_KG = 450.0
PARSER_V = 1           # sobe quando muda a leitura: o cache inteiro é lido de novo
SEMANAS = 53           # domingos para trás (12 meses + 1 semana)
REFAZ_DIAS = 35        # fotografias mais novas que isto são lidas de novo (lançamentos com data retroativa)
OPS = {1: 'Entrada', 2: 'Saída', 3: 'Morte', 4: 'Transferência', 5: 'Mudança de piquete', 6: 'Pesagem', 7: 'Ajuste', 8: 'Previsão'}

# nome da fazenda no ControlPasto -> chave do site (o resto é casado pelo nome normalizado)
APELIDOS = {'lusipar central': 'lusipar', 'lusipar 1': 'lusipar', 'lusipar 2': 'lusipar', 'yby pora': 'ybypora',
            'cafetalera': 'yby-pyta', 'yby pyta': 'yby-pyta', 'santa maria da serra': 'santa-maria',
            'santa maria': 'santa-maria', 'itavera': 'ita-vera', 'ita vera': 'ita-vera'}

def sem_acento(s):
    return unicodedata.normalize('NFD', str(s or '')).encode('ascii', 'ignore').decode()
def norm_faz(s):
    s = sem_acento(s).lower(); s = re.sub(r'\b(estancia|fazenda|establecimiento|hacienda)\b', ' ', s)
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s)).strip()
def norm_piq(s):
    s = sem_acento(s).upper(); s = re.sub(r'\b(PIQUETE|PIQ|PASTO|POTRERO|PASTURA)\b\.?', '', s)
    s = re.sub(r'[\s_.\-/]+', '', s); return re.sub(r'^0+(?=\d)', '', s)
def base_piq(s):  # 'E13-norte' -> 'E13'
    return norm_piq(re.sub(r'[\s\-_]*(norte|sul|leste|oeste|nova|novo|velho|a\.?n\.?)$', '', str(s), flags=re.I))

def site_key(fz_cp):
    n = norm_faz(fz_cp)
    if n in APELIDOS: return APELIDOS[n]
    for k, v in CFG.items():
        if norm_faz(v['nome']) == n or norm_faz(k.replace('-', ' ')) == n: return k
    for k, v in CFG.items():  # 'Yby Pytá (Cafetalera)' etc.
        nm = norm_faz(re.sub(r'\(.*?\)', '', v['nome']))
        if nm and (nm == n or n.startswith(nm + ' ')): return k
    return None

# ------------------------------------------------------------------ cifra
def chave():
    return hashlib.pbkdf2_hmac('sha256', SENHA.encode(), b'ndvi-fazendas:lotacao', 150000, 32)
def cifrar(obj, k):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12); ct = AESGCM(k).encrypt(iv, json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode(), None)
    return {'v': 1, 'iv': base64.b64encode(iv).decode(), 'ct': base64.b64encode(ct).decode()}
def decifrar(env, k):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return json.loads(AESGCM(k).decrypt(base64.b64decode(env['iv']), base64.b64decode(env['ct']), None))

# ------------------------------------------------------------------ API
class CP:
    def __init__(self):
        import requests
        self.s = requests.Session(); self.sid = None; self.req = requests
    def _post(self, body, timeout):
        for k in range(4):
            try:
                r = self.s.post(URL, json=body, timeout=timeout)
                if r.status_code >= 500: time.sleep(10 * (k + 1)); continue
                r.raise_for_status(); return r.json()
            except (self.req.ConnectionError, self.req.Timeout):
                if k == 3: raise
                time.sleep(10 * (k + 1))
        raise RuntimeError('ControlPasto sem resposta')
    def call(self, method, params, timeout=300):
        body = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if self.sid: body['id'] = self.sid
        j = self._post(body, timeout)
        if j.get('error'):
            e = j['error']; raise RuntimeError(f"{method}: {e.get('message') if isinstance(e, dict) else e}")
        return j.get('result')
    def login(self):
        j = self._post({'jsonrpc': '2.0', 'method': 'LoginService.Login', 'params': {'UserName': USER, 'Password': PASS}}, 120)
        if j.get('error'):
            e = j['error']; raise RuntimeError('login recusado: ' + str(e.get('message') if isinstance(e, dict) else e))
        txt = json.dumps(j.get('result')); m = re.search(r'\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?', txt)
        if not m: m = re.search(r'\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?', json.dumps(j.get('id')))
        if not m: raise RuntimeError('login sem id de sessão na resposta (campos: ' + ','.join(_chaves(j)) + ')')
        g = m.group(0); self.sid = g if g.startswith('{') else '{' + g + '}'
    def logout(self):
        try: self.call('LoginService.Logout', {}, 60)
        except Exception: pass

def _chaves(x):
    return list(x.keys()) if isinstance(x, dict) else [type(x).__name__]

# ------------------------------------------------------------------ diagnóstico (só nomes de campos)
def esquema(x, nome='resultado', pre='', prof=0, out=None):
    out = [] if out is None else out
    if prof > 14: return out
    if isinstance(x, list):
        out.append(f'{pre}{nome}: lista[{len(x)}]')
        dicts = [e for e in x[:50] if isinstance(e, dict)]
        if dicts:
            m = {}
            for d in dicts:
                for k, v in d.items(): m.setdefault(k, v)
            esquema(m, '[item]', pre + '  ', prof + 1, out)
        elif x: out.append(f'{pre}  [item]: {type(x[0]).__name__}')
    elif isinstance(x, dict):
        out.append(f'{pre}{nome}: {{}}')
        for k, v in x.items():
            if isinstance(v, (list, dict)): esquema(v, k, pre + '  ', prof + 1, out)
            else:
                ex = ''
                if isinstance(v, str) and re.search(r'nome|desc|faz|piq|past|lote|categ|oper|tipo', k, re.I): ex = f' ex.: {v[:40]!r}'
                out.append(f'{pre}  {k}: {type(v).__name__}{ex}')
    return out

def grava_debug(brutos):
    pub = ROOT / 'config' / 'controlpasto_debug_pub.pem'
    if not pub.exists(): return
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, v in brutos.items(): z.writestr(n + '.json', json.dumps(v, ensure_ascii=False, indent=1))
    k = AESGCM.generate_key(256); iv = os.urandom(12); ct = AESGCM(k).encrypt(iv, buf.getvalue(), None)
    pk = serialization.load_pem_public_key(pub.read_bytes())
    wk = pk.encrypt(k, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    (ROOT / 'cp_debug.bin').write_bytes(len(wk).to_bytes(2, 'big') + wk + iv + ct)
    print('cp_debug.bin gravado (criptografado)')

# ------------------------------------------------------------------ leitura genérica da árvore
NIVEL = [('fz', r'faz|establec|estancia'), ('piq', r'piq'), ('pasto', r'past|potr'), ('lote', r'lote')]
def nivel_da_chave(k):
    for nv, rx in NIVEL:
        if re.search(rx, k, re.I): return nv
    return None
def campo(d, *rx, tipo=None, evita=None):
    for r in rx:
        for k, v in d.items():
            if v is None or v == '': continue
            if evita and re.search(evita, k, re.I): continue
            if re.search(r, k, re.I) and (tipo is None or isinstance(v, tipo)) and not isinstance(v, bool): return v
    return None
def nome_no(d, nv):
    alvo = {'fz': r'faz', 'piq': r'piq', 'pasto': r'past', 'lote': r'lote'}[nv]
    v = campo(d, rf'^(nome|desc\w*|name)_?{alvo}', rf'{alvo}\w*(nome|desc|name)', r'^(nome|name|descricao|descri[cç][aã]o|desc|ds|nm)$', tipo=str)
    if v is None: v = campo(d, rf'^{alvo}\w*$', tipo=str)
    if v is None: v = campo(d, r'nome|desc|name', tipo=str)
    return v
def num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool): return float(v)
    if isinstance(v, str):
        t = v.strip().replace('.', '').replace(',', '.') if re.match(r'^-?[\d.]+,\d+$', v.strip()) else v.strip()
        try: return float(t)
        except ValueError: return None
    return None
RX_CAB = (r'^(qt|qtd|qtde|quant)\w*(cab|anim)', r'^(qtd|qtde|qt|quantidade|cabecas|cabe[cç]as|nanimais|animais|saldo|estoque|cab)$', r'^(qt|qtd|qtde|quant)')
RX_PV = (r'peso\w*med', r'^(pm|pmed|pesomedio|peso_medio|peso)$', r'peso')
RX_UA = (r'^(ua|uas|totalua|ua_total|qtdua)$', r'^ua')

def folhas(x, ctx=None, nv=None, out=None):
    """percorre a árvore; devolve [(contexto por nível, campos simples da folha)]"""
    out = [] if out is None else out; ctx = ctx or {}
    if isinstance(x, list):
        for e in x: folhas(e, ctx, nv, out)
    elif isinstance(x, dict):
        esc = {k: v for k, v in x.items() if not isinstance(v, (list, dict))}
        filhos = [(k, v) for k, v in x.items() if isinstance(v, (list, dict)) and v]
        c2 = dict(ctx)
        if nv: c2[nv] = esc
        else: c2['_'] = {**ctx.get('_', {}), **esc}
        if filhos:
            for k, v in filhos: folhas(v, c2, nivel_da_chave(k) or nv, out)
        else: out.append((c2, esc))
    return out

def lotes_de(res):
    """carga instantânea -> [[fz, pasto, piquete, lote, cab, pv, ua, categoria]]"""
    rows = []
    for ctx, f in folhas(res):
        cab = num(campo(f, *RX_CAB, evita=r'piq|past|faz|dias|capac'))
        if not cab or cab <= 0: continue
        sob = {**ctx.get('_', {}), **f}
        get = lambda nv, rx: (nome_no(ctx[nv], nv) if nv in ctx and ctx[nv] is not f else None) or campo(sob, rx, tipo=str)
        fz = get('fz', r'faz|estab'); pa = get('pasto', r'^(nome_?)?past'); pq = get('piq', r'piq'); lo = get('lote', r'lote')
        if 'lote' in ctx and ctx['lote'] is f: lo = nome_no(f, 'lote') or lo
        pv = num(campo(f, *RX_PV, evita=r'total|entr')); ua = num(campo(f, *RX_UA))
        if ua is None and pv: ua = cab * pv / UA_KG
        cat = campo(f, r'categ', tipo=str)
        rows.append([str(fz or ''), str(pa or ''), str(pq or ''), str(lo or ''), cab, pv, ua, cat or ''])
    return rows

DATA_RX = re.compile(r'^(\d{4})-(\d{2})-(\d{2})|^(\d{2})/(\d{2})/(\d{4})')
def iso(v):
    m = DATA_RX.match(str(v or ''))
    if not m: return None
    return f'{m[1]}-{m[2]}-{m[3]}' if m[1] else f'{m[6]}-{m[5]}-{m[4]}'
def movs_de(res):
    """histórico -> [[data, fz, piq, op, lote, cab, peso, outro_piq, obs]] (piq = pasto onde aconteceu)"""
    rows = []
    for ctx, f in folhas(res):
        todos = dict(ctx.get('_', {}))
        for nv in ('fz', 'pasto', 'piq', 'lote'):
            if nv in ctx: todos.update({f'{nv}.{k}': v for k, v in ctx[nv].items()})
        todos.update(f)
        d = None
        for k, v in f.items():
            if re.search(r'data|dt|date', k, re.I) and iso(v): d = iso(v); break
        if not d:
            for k, v in todos.items():
                if re.search(r'data|dt|date', k, re.I) and iso(v): d = iso(v); break
        if not d: continue
        op = campo(f, r'^(cod\w*)?oper', r'^tipo', r'oper|movim')
        opn = num(op); opt = OPS.get(int(opn), str(op)) if opn is not None else str(op or '')
        cab = num(campo(f, *RX_CAB, evita=r'piq|past|faz|dias')); peso = num(campo(f, *RX_PV, evita=r'total'))
        fz = (nome_no(ctx['fz'], 'fz') if 'fz' in ctx else None) or campo(todos, r'faz|estab', tipo=str)
        po = campo(f, r'piq\w*orig|orig\w*piq', tipo=str); pd = campo(f, r'piq\w*dest|dest\w*piq', tipo=str)
        pq = (nome_no(ctx['piq'], 'piq') if 'piq' in ctx else None) or campo(f, r'^(nome_?)?piq\w*$', tipo=str, evita=r'orig|dest')
        lo = (nome_no(ctx['lote'], 'lote') if 'lote' in ctx else None) or campo(todos, r'lote', tipo=str)
        obs = campo(f, r'obs|hist|coment', tipo=str) or ''
        base = [d, str(fz or ''), None, opt, str(lo or ''), cab, peso, None, obs[:120]]
        if po or pd:  # mudança: aparece nos dois pastos
            if po: r = list(base); r[2], r[7] = po, (f'→ {pd}' if pd else ''); rows.append(r)
            if pd: r = list(base); r[2], r[7] = pd, (f'← {po}' if po else ''); rows.append(r)
        else:
            r = list(base); r[2] = pq or ''; rows.append(r)
    rows.sort(key=lambda r: r[0]); return rows

# ------------------------------------------------------------------ pastos do site
def carrega_pastos(key):
    p = ROOT / CFG[key]['pastos']
    if not p.exists(): return []
    return [f['properties'] for f in json.loads(p.read_text(encoding='utf-8'))['features']]

class Casador:
    """casa (fazenda CP, pasto CP, piquete CP) com o pasto do mapa; um piquete pode cair em várias partes (norte/sul)"""
    def __init__(self, key, props):
        self.key = key; self.props = props; self.ex = {}; self.base = {}
        multi = len({p.get('retiro') for p in props}) > 1
        for p in props:
            if p.get('area_ha', 0) > 5000: continue  # limite legal inteiro não é pasto
            n = norm_piq(p['nome'])
            self.ex.setdefault(n, []).append(p); self.base.setdefault(base_piq(p['nome']), []).append(p)
            if multi: self.ex.setdefault(norm_faz(p.get('retiro')) + '|' + n, []).append(p)
    @staticmethod
    def _fz(fz):
        n = norm_faz(fz); return {'lusipar central': 'lusipar'}.get(n, n)
    def partes(self, fz, pa, pq):
        for nome in (pq, pa):
            if not nome: continue
            n = norm_piq(nome)
            for k in (self._fz(fz) + '|' + n, n):
                if k in self.ex: return self.ex[k]
            if n in self.base: return self.base[n]
            b = base_piq(nome)
            if b in self.base: return self.base[b]
        return None

def domingos(hoje):
    d = hoje - dt.timedelta(days=(hoje.weekday() + 1) % 7)
    return [(d - dt.timedelta(weeks=i)).isoformat() for i in range(SEMANAS)][::-1]

# ------------------------------------------------------------------ montagem por fazenda
def monta(key, hoje_s, datas, snaps, movs, atualizado):
    props = carrega_pastos(key); cas = Casador(key, props)
    area = {p['id']: p.get('area_ha') or 0 for p in props}
    P = {}; sem = {}
    def soma(alvo, i, cab, ua):
        s = alvo.setdefault('s', [[0, 0] for _ in datas]); s[i][0] += cab; s[i][1] += ua
    tot = {'s': [[0, 0] for _ in datas]}
    for i, d in enumerate(datas):
        for fz, pa, pq, lo, cab, pv, ua, cat in snaps.get(d, []):
            if site_key(fz) != key: continue
            ua = ua or 0; tot['s'][i][0] += cab; tot['s'][i][1] += ua
            partes = cas.partes(fz, pa, pq)
            if not partes:
                if d == hoje_s: sem.setdefault((fz, pa, pq), []).append([lo, cab, pv, ua, cat])
                continue
            A = sum(area[p['id']] for p in partes) or 1
            for p in partes:
                w = area[p['id']] / A; e = P.setdefault(p['id'], {'cp': pq or pa})
                soma(e, i, cab * w, ua * w)
                if d == hoje_s: e.setdefault('lotes', []).append([lo, round(cab * w), pv, round(ua * w, 1), cat])
    for pid, e in P.items():
        ha = area.get(pid) or 0; s = e.get('s', [[0, 0] for _ in datas])
        e['s'] = [[round(c), round(u, 1)] for c, u in s]
        e['cab'], e['ua'] = e['s'][-1]; e['lot'] = round(e['ua'] / ha, 3) if ha else None
        lots = [u / ha for c, u in s[:-1]] if ha else []
        e['m12'] = round(sum(lots) / len(lots), 3) if lots else None
        e.setdefault('lotes', [])
    for p in props:  # pastos sem lote hoje: vazio, mas com média
        if p['id'] not in P and p.get('area_ha', 0) <= 5000:
            P[p['id']] = {'cp': None, 'lotes': [], 'cab': 0, 'ua': 0, 'lot': 0, 'm12': 0, 's': [[0, 0] for _ in datas]}
    ha_t = sum(v for k, v in area.items() if v <= 5000)
    tot['s'] = [[round(c), round(u, 1)] for c, u in tot['s']]
    tot.update({'cab': tot['s'][-1][0], 'ua': tot['s'][-1][1], 'ha': round(ha_t, 1),
                'lot': round(tot['s'][-1][1] / ha_t, 3) if ha_t else None,
                'm12': round(sum(u for c, u in tot['s'][:-1]) / max(1, len(tot['s']) - 1) / ha_t, 3) if ha_t else None})
    M = {}; n_sem = 0
    for d, fz, pq, op, lo, cab, peso, outro, obs in movs:
        if site_key(fz) != key: continue
        partes = cas.partes(fz, None, pq)
        if not partes: n_sem += 1; continue
        for p in partes: M.setdefault(p['id'], []).append([d, op, lo, cab, peso, outro or '', obs])
    return {'fazenda': key, 'atualizado': atualizado, 'hoje': hoje_s, 'ua_kg': UA_KG, 'datas': datas, 'pastos': P, 'total': tot,
            'sem_mapa': [[fz, pa, pq, ls] for (fz, pa, pq), ls in sorted(sem.items())], 'movs': M, 'movs_sem_mapa': n_sem}

# ------------------------------------------------------------------ dados de exemplo
def fake_snaps(datas):
    rnd = random.Random(7); snaps = {d: [] for d in datas}; movs = []
    for key, v in CFG.items():
        props = [p for p in carrega_pastos(key) if p.get('area_ha', 0) <= 5000]
        fz = {'lusipar': None, 'yby-pyta': 'Cafetalera', 'ybypora': 'YBY PORÃ'}.get(key, v['nome'])
        for j, p in enumerate(props):
            if rnd.random() < .35: continue
            ph = rnd.random() * 6.28; cap = p['area_ha'] * rnd.uniform(1.0, 2.6) * 450 / 380
            lote = f'L{rnd.randint(100, 999)}'; f = fz or p.get('retiro')
            for i, d in enumerate(datas):
                occ = (i // 4 + j) % 3 != 0
                if occ: snaps[d].append([f, p.get('retiro', ''), p['nome'], lote, round(cap * (0.8 + 0.2 * abs((i + ph) % 6 - 3) / 3)), 300 + i * 2.5, None, 'Novilho'])
                prev = (max(0, i - 1) // 4 + j) % 3 != 0
                if i and occ != prev: movs.append([d, f, p['nome'], 'Mudança de piquete', lote, round(cap), 330 + i * 2, '← vizinho' if occ else '→ vizinho', ''])
    for r in (r for s in snaps.values() for r in s): r[6] = r[4] * r[5] / UA_KG
    return snaps, sorted(movs)

# ------------------------------------------------------------------ principal
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    agora = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3)))
    hoje = agora.date(); hoje_s = hoje.isoformat(); datas = [d for d in domingos(hoje) if d != hoje_s] + [hoje_s]
    if not FAKE and not (USER and PASS):
        print('Sem CONTROLPASTO_USUARIO / CONTROLPASTO_CLAVE: lotação não atualizada (crie os dois segredos no repositório).')
        return
    k = chave(); idx_p = OUT / 'index.json'
    cache = {'pv': PARSER_V, 'snaps': {}}
    cp_ = OUT / '_cache.json'
    if cp_.exists():
        try:
            c = decifrar(json.loads(cp_.read_text()), k)
            if c.get('pv') == PARSER_V: cache = c
        except Exception as e: print('cache ilegível (senha mudou?), lendo tudo de novo:', type(e).__name__)
    erro = None
    if FAKE:
        snaps, movs = fake_snaps(datas)
    else:
        api = CP(); brutos = {}
        try:
            api.login(); print('login ok')
            snaps = {}
            limite = (hoje - dt.timedelta(days=REFAZ_DIAS)).isoformat()
            for d in datas:
                if d in cache['snaps'] and d < limite and d != hoje_s: snaps[d] = cache['snaps'][d]; continue
                t = time.time(); res = api.call('ControlPastoService.LstFz_CargaInst', {'Data': d})
                if d == hoje_s:
                    brutos['carga_hoje'] = res
                    print('\n'.join(esquema(res, 'LstFz_CargaInst')[:160]))
                snaps[d] = lotes_de(res)
                print(f'carga {d}: {len(snaps[d])} lotes, {sum(r[4] for r in snaps[d]):.0f} cab ({time.time()-t:.1f} s)')
                time.sleep(0.5)
            d0 = (hoje - dt.timedelta(days=380)).isoformat(); res = None
            try:
                res = api.call('ControlPastoService.LstFz_HistLote', {'DataIni': d0, 'DataFim': hoje_s}, 600)
                movs = movs_de(res)
            except Exception as e:  # período grande demais: em trimestres
                print('histórico em um pedido falhou:', e); movs = []; a = hoje - dt.timedelta(days=380)
                while a < hoje:
                    b = min(hoje, a + dt.timedelta(days=92))
                    r2 = api.call('ControlPastoService.LstFz_HistLote', {'DataIni': a.isoformat(), 'DataFim': b.isoformat()}, 600)
                    res = res or r2; movs += movs_de(r2); a = b + dt.timedelta(days=1); time.sleep(0.5)
                movs.sort(key=lambda r: r[0])
            brutos['hist'] = res
            print('\n'.join(esquema(res, 'LstFz_HistLote')[:120]))
            print(f'movimentações: {len(movs)}')
            if DEBUG or not snaps.get(hoje_s): grava_debug(brutos)
        finally:
            api.logout()
        if not snaps.get(hoje_s):
            erro = 'a carga de hoje veio sem lotes reconhecidos'
            print('AVISO:', erro)
    faz_cp = sorted({r[0] for r in snaps.get(hoje_s, [])})
    print('fazendas no ControlPasto:', ', '.join(f'{f} -> {site_key(f) or "(sem mapa no site)"}' for f in faz_cp))
    feitas = []
    for key in CFG:
        if not any(site_key(f) == key for f in faz_cp): continue
        D = monta(key, hoje_s, datas, snaps, movs, agora.isoformat(timespec='minutes'))
        cas = sum(1 for e in D['pastos'].values() if e['cab']); sm = sum(len(x[3]) for x in D['sem_mapa'])
        print(f'{key}: {D["total"]["cab"]} cab, {D["total"]["ua"]} UA, {cas} pastos ocupados, {sm} lotes sem pasto no mapa, {len(D["movs"])} pastos com movimentações')
        for fz, pa, pq, ls in D['sem_mapa'][:15]: print(f'   sem mapa: {fz} / {pa} / {pq}')
        (OUT / f'{key}.json').write_text(json.dumps(cifrar(D, k), separators=(',', ':')), encoding='utf-8'); feitas.append(key)
    if not FAKE and not erro:
        cache['snaps'] = {d: v for d, v in snaps.items() if d != hoje_s}
        cp_.write_text(json.dumps(cifrar(cache, k), separators=(',', ':')), encoding='utf-8')
    idx = {'atualizado': agora.isoformat(timespec='minutes'), 'fazendas': feitas, 'fonte': 'ControlPasto (Integrasoftware)' + (' — EXEMPLO' if FAKE else ''),
           'cifra': 'AES-256-GCM; chave PBKDF2-SHA256(senha do site, "ndvi-fazendas:lotacao", 150000)'}
    if erro: idx['erro'] = erro
    idx_p.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding='utf-8')

if __name__ == '__main__':
    main()
