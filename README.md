# NDVI automático das fazendas

Mapa de NDVI (índice de vegetação) das fazendas, atualizado sozinho todo dia com as passagens mais
recentes do **Sentinel-2** (10 m, revisita de ~5 dias), com o NDVI médio de cada pasto e a série histórica.

**Página:** https://lucasmoller9646.github.io/ndvi-fazendas/

## Como funciona

| Etapa | O que acontece |
|---|---|
| Agendamento | `.github/workflows/ndvi.yml` roda todo dia às 12:17 (horário de Assunção/Brasília) e também pelo botão **Run workflow** em *Actions*. |
| Catálogo | `ndvi/run.py` pergunta ao Copernicus Data Space quais passagens do Sentinel-2 L2A cobrem cada fazenda e ainda não foram processadas. |
| NDVI | Para cada data, pede ao Sentinel Hub (Process API) o NDVI já recortado no retângulo da fazenda, com nuvem, sombra e cirrus mascarados pela classificação de cena (SCL). |
| Estatística | Calcula NDVI médio, P10 e P90 e a fração sem nuvem de cada pasto. Pasto com menos de 60% de área limpa não entra na série daquela data. |
| Mapa | Se pelo menos 60% da fazenda estiver limpa, salva a imagem colorida. Os mapas ficam no ramo `imagens`, regravado a cada execução sem histórico (para o repositório não crescer sem limite); guardam-se os últimos 30–40 por fazenda. A série por pasto fica inteira no ramo `main`. |
| Relevo | Na primeira vez, `ndvi/relevo.py` grava o modelo de elevação e as curvas de nível de cada fazenda em `docs/relevo/`. |
| Publicação | Grava `docs/data/<fazenda>.json` no repositório e publica `docs/` no GitHub Pages. |

## Fontes de imagem

| Satélite | Resolução | Acesso | Revisita sobre as fazendas |
|---|---|---|---|
| Sentinel-2 A/B/C (L2A) | 10 m | Copernicus Data Space (credenciais nos segredos; cota gratuita de 10.000 unidades/mês) | ~5 dias por órbita, com sobreposição de órbitas em parte das fazendas |
| Landsat 8 e 9 (Collection 2 Level-2) | 30 m | Microsoft Planetary Computer — público, sem conta e sem cota | 8 dias combinados por trilha; as trilhas 225 e 226 se sobrepõem na região |

As duas fontes usam reflectância de superfície corrigida da atmosfera, então o NDVI é comparável (diferença típica de
0,01–0,02). Quando os dois satélites passam no mesmo dia, fica a imagem do Sentinel-2. No gráfico da série, ● é
Sentinel-2 e ○ é Landsat. Para desligar o Landsat numa fazenda, use `"landsat": false` em `config/fazendas.json`.

## Fazendas

| Chave | Fazenda | Unidades | Histórico desde |
|---|---|---|---|
| ypoti | Ypoti | 66 pastos (blocos A e C) + limite legal | 01/06/2026 |
| lusipar | Lusipar | 71 pastos (Lusipar, Lusipar 1, Lusipar 2) | 01/06/2026 |
| santa-clara | Santa Clara | 33 piquetes + área nova B16–B31 | 01/06/2026 |
| ybypora | Ybypora | 37 piquetes | 01/06/2026 |
| oro-verde | Oro Verde | 15 piquetes | 01/06/2026 |
| yby-pyta | Yby Pytá (Cafetalera) | 21 pastos | 01/06/2026 |
| la-esperanza | La Esperanza | 138 pastos com nome | 01/11/2025 |
| ita-vera | Ita Vera | 33 pastos | 01/06/2026 |
| cerro-memby | Cerro Memby | 51 pastos + sede | 01/06/2026 |
| luna-blanca | Luna Blanca | fazenda inteira | 01/06/2026 |
| cielo-azul | Cielo Azul | fazenda inteira (rascunho) | 01/06/2026 |
| santa-maria | Santa María | limite legal inteiro | 01/06/2026 |

Para rodar só algumas fazendas, use **Run workflow** e preencha o campo com as chaves separadas por vírgula.

Na primeira execução o histórico é preenchido desde a data `inicio` de `config/fazendas.json`.
Depois disso, cada execução só olha os últimos 20 dias.

## Avanço das áreas de trabalho (La Esperanza)

`config/la-esperanza_obras.geojson` traz as 16 subáreas de abertura (1A a 5C, encaixadas no miolo). Para cada imagem com
pelo menos 90% da fazenda sem nuvem, `ndvi/obras.py` marca como **trabalhado** o pixel que aparece como solo exposto
(0 ≤ NDVI < 0,35) a partir de `inicio` e que não era solo antes (estradas, currais, aguadas). Também calcula a parte
com **solo exposto agora** e a parte já trabalhada que ficou **verde de novo** (NDVI ≥ 0,55). O estado pixel a pixel fica em
`docs/img/_estado/` (ramo `imagens`). `ndvi/backfill_obras.py` reconstrói tudo a partir dos mapas já guardados.

## Relevo (curvas de nível e 3D)

`ndvi/relevo.py` recorta o **Copernicus DEM GLO-30** (modelo de elevação de 30 m da ESA/Airbus, lido do Microsoft
Planetary Computer, público e sem conta; se faltar, usa o NASADEM) no mesmo retângulo do NDVI de cada fazenda e grava em
`docs/relevo/`: o modelo em PNG (altitude em decímetros codificada em RGB), as curvas de nível em GeoJSON e um JSON com a
altitude mínima e máxima. A equidistância das curvas é escolhida pelo desnível da fazenda (1, 2, 5, 10 m…), com uma curva
mestra a cada cinco. Como o relevo não muda, cada fazenda só é processada uma vez (ou de novo com `RELEVO_FORCE=1`, ou se o
retângulo mudar); fazenda nova ganha o relevo na primeira execução do workflow.

Na página: **Curvas de nível** liga as curvas sobre o mapa (rótulos a partir do zoom 13) e o canto inferior esquerdo mostra a
altitude sob o cursor. **Relevo 3D** abre o terreno em 3D com o NDVI da imagem selecionada ou com cores de altitude, as
curvas, os pastos e os nomes, com exagero vertical de 1 a 10 vezes. O DEM de 30 m mede a superfície (inclui a altura das
matas), com erro vertical típico de 2 a 4 m: serve para planejar e orientar, não substitui levantamento topográfico.

## Senha da página

A página pede uma senha de 6 dígitos antes de mostrar os mapas. A senha padrão é **123321**. Para trocar, crie (ou edite)
o segredo `SENHA_SITE` em **Settings → Secrets and variables → Actions** com os 6 dígitos novos e rode o workflow:
a página passa a aceitar só a senha nova. Quem marcou "lembrar neste aparelho" precisa digitar a nova senha.

Atenção: é uma senha de entrada, não uma proteção dos dados. O repositório é público, e quem souber o endereço dos
arquivos (`docs/data/...`, ramo `imagens`) consegue baixá-los sem passar pela senha.

## Exportar

O botão **Exportar** da página gera, para a imagem selecionada: GeoTIFF com o valor do NDVI em cada pixel (EPSG:4326,
nodata −9999), KMZ para o Google Earth, PNG colorido com .pgw/.prj, tabela CSV dos pastos e a série histórica completa
em CSV (separador `;`, decimal com vírgula). Em La Esperanza, também o avanço das áreas de trabalho em CSV.

Os mapas ficam guardados como PNG de 8 bits com o próprio NDVI (0 = sem dado; 1–255 = −0,2 a 0,95, passo ≈ 0,0045),
e a página colore no navegador. Por padrão a escala é de **máximo contraste** (do percentil 2 ao 98 dos pastos na
imagem); a opção "Fixa" mantém as mesmas cores para o mesmo NDVI em todas as datas. Os mapas anteriores a 23/11/2026
foram convertidos dos PNGs coloridos antigos e têm precisão menor (≈ 0,03).

## Credenciais (segredos do repositório)

Em **Settings → Secrets and variables → Actions → New repository secret**:

- `CDSE_CLIENT_ID` — Client ID criado em https://shapps.dataspace.copernicus.eu/dashboard (User settings → OAuth clients)
- `CDSE_CLIENT_SECRET` — o Client secret correspondente

Cota gratuita do Copernicus: 10.000 unidades de processamento por mês. Cada passagem de La Esperanza consome
cerca de 10 unidades, então o histórico inicial (~1 ano) e o uso diário ficam muito abaixo do limite.

## Adicionar outra fazenda

1. Exportar os pastos em GeoJSON (EPSG:4326) com as propriedades `id` (único), `retiro`, `nome` e `area_ha`.
2. Colocar o arquivo em `config/` e acrescentar a fazenda em `config/fazendas.json`.
3. Rodar o workflow: a fazenda aparece no seletor da página.

## Teste local sem internet

```bash
pip install -r requirements.txt
FAKE=1 python ndvi/run.py      # gera dados sintéticos em docs/
python -m http.server -d docs  # abrir http://localhost:8000
```

Fonte dos dados: Copernicus Sentinel-2 (contém dados modificados Copernicus Sentinel), processados via Copernicus Data Space Ecosystem.
