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
| Mapa | Se pelo menos 60% da fazenda estiver limpa, salva a imagem colorida em `docs/img/`. Os últimos 40 mapas ficam guardados; a série por pasto é mantida inteira. |
| Publicação | Grava `docs/data/<fazenda>.json` no repositório e publica `docs/` no GitHub Pages. |

Na primeira execução o histórico é preenchido desde a data `inicio` de `config/fazendas.json`.
Depois disso, cada execução só olha os últimos 20 dias.

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
