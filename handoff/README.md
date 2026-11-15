# Gaiapus — pacote de integração (modelos v7)

Este pacote contém o necessário para o backend e o frontend do Gaiapus usarem os modelos de rendimento, a projeção de risco hídrico e o caso Field Shift. Ele é autocontido: não precisa dos dados de pesquisa nem de GPU. Em uma máquina nova, com Python e internet, siga as seções na ordem.

> **Aviso para o app.** As estimativas são **retrospectivas**. Foram validadas em 2019–2021 e testadas uma única vez em 2022–2024, e **não estão liberadas para uso operacional**. Mostre esse rótulo junto ao número. Os resultados do teste estão no catálogo e em cada resposta.

---

## 1. O que o Gaiapus entrega

| Produto | Pergunta que responde | Como é calculado |
|---|---|---|
| **Estimativa de rendimento** | "Quanto deve render (kg/ha) esta cultura neste município nesta safra, com os dados até hoje?" | Modelo por cultura com clima até o corte, balanço hídrico e rendimentos publicados de anos anteriores |
| **Projeção de risco hídrico** | "Como ficam a água no solo, o déficit e o estresse nos próximos N dias?" | Balanço hídrico FAO-56 com a previsão ECMWF do dia (N avaliado até 7 dias) |
| **Caso Field Shift (Sorriso)** | "Soja → milho safrinha ou soja → milho + braquiária?" | Simulação de cenários com hipóteses explícitas (JSON pronto) |

São produtos separados. A projeção hídrica **não** é convertida em kg/ha.

Culturas: soja, milho 2ª safra, milho 1ª safra, trigo, arroz, feijão 1ª safra, feijão 2ª safra, café arábica (MG), café conilon (ES) e café total MG. São cerca de 2.100 municípios em 16 UFs. A lista exata está no catálogo.

---

## 2. Conteúdo do pacote

```
gaiapus-integracao/
├── README.md                 ← este guia
├── requirements.txt
├── smoke_test.py             ← verificação rápida da instalação
├── example_api.py            ← exemplo de API (FastAPI) para copiar ou adaptar
├── run_daily.ps1 / run_daily.sh   ← job diário (Windows / Linux)
├── data/
│   ├── serving/              ← código de inferência: yield_service, providers, daily_job, products …
│   ├── ingest/  models/      ← funções reutilizadas (features, balanço hídrico)
│   ├── raw/…/locations.json  ← coordenadas dos municípios cobertos
│   ├── processed/water_projection_v1/   ← backtest e climatologia da projeção hídrica
│   └── frozen/               ← regra regional e resultado do teste (registro)
├── dist/gaiapus-models-v7/
│   ├── catalog.json          ← culturas, municípios, cortes, modelo, erros e teste
│   ├── models_manifest.json  ← SHA-256 e paridade de cada modelo
│   └── models/               ← modelos portáteis (JSON; o MLP também tem .pt)
└── contracts/                ← JSON Schemas das respostas e exemplos
```

---

## 3. Instalação

Requisitos: **Python 3.11 ou mais novo**, internet e cerca de 200 MB de disco para o cache.

Windows (PowerShell):

```powershell
cd gaiapus-integracao
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu   # PyTorch só CPU (menor)
pip install -r requirements.txt
```

Linux/macOS:

```bash
cd gaiapus-integracao
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### 3.1 Credencial NASA (obrigatória para o modo ao vivo)

A chuva diária (IMERG Late) vem do GES DISC da NASA:

1. Crie uma conta gratuita em <https://urs.earthdata.nasa.gov/>.
2. Em **Applications → Authorized Apps**, autorize **"NASA GESDISC DATA ARCHIVE"**.
3. Em **Generate Token**, gere um token (vale cerca de 60 dias).
4. Guarde o token na variável de ambiente `EARTHDATA_TOKEN`. Não coloque o token no Git nem em arquivos do projeto.

```powershell
[Environment]::SetEnvironmentVariable('EARTHDATA_TOKEN','<token>','User')   # Windows; reabra o terminal
```
```bash
export EARTHDATA_TOKEN='<token>'   # Linux; ou no arquivo de ambiente do serviço
```

NASA POWER, Open-Meteo e IBGE/SIDRA não precisam de chave.

### 3.2 Verificação

```bash
python smoke_test.py          # modelos, catálogo e contratos (sem internet)
python smoke_test.py --live   # inclui uma estimativa ao vivo e uma projeção hídrica (internet + token)
```

O resultado esperado é `RESULTADO: tudo certo`.

---

## 4. Como colocar no ar

### 4.1 Arquitetura recomendada

```
 job diário (06:30 UTC) ──► NASA POWER, IMERG Late, ERA5 (Open-Meteo), IBGE SIDRA
        │   python -m data.serving.daily_job --mode live
        ▼
 dist/snapshots_live/<data>/yield/<cultura>.json   (uma resposta por município)
 dist/snapshots_live/<data>/water/<município>_<cultura>.json
 dist/snapshots_live/<data>/manifest.json   e   dist/snapshots_live/latest.json
        │
        ▼
 backend ── lê o instantâneo mais recente ──► frontend
```

**Por que um job diário e não um cálculo a cada clique:**

- As fontes são diárias. Os dados mais novos chegam com 1 a 6 dias de atraso, então recalcular a cada clique não traz informação nova.
- O cálculo é leve: cerca de 2,5 s por município depois que o cache está aquecido. O que limita são as APIs externas, que têm cotas.
- O backend só lê arquivos JSON. Responde em milissegundos e não cai quando uma API externa fica fora do ar; nesse caso, serve o último instantâneo completo.

A exceção é quando o produtor informa a própria data de semeadura. Esse caso é calculado na hora, com `POST /v1/yield` no exemplo (2–3 s).

### 4.2 Primeira execução e agendamento

```bash
python -m data.serving.daily_job --mode live      # primeira vez: ~30 min + carga da chuva (ver abaixo)
```

Medido em 14/11/2026: o job completo levou **27 min** e gerou cerca de 4.500 respostas (1.801 prontas, 2,7% com `inputs_incomplete` por causa da cota de radiação na carga inicial). Isso foi com o cache de chuva já carregado. Antes, a carga da chuva IMERG para todo o Brasil leva ~1,5–2,5 s por dia de dado (cerca de 25 min para os ~560 dias do café), uma única vez. Nas execuções seguintes, só o dia novo é baixado.

Agende depois das 06:00 UTC:

- **Windows:** Agendador de Tarefas → Criar Tarefa → Disparador "Diariamente 03:30" (horário de Brasília) → Ação `powershell -ExecutionPolicy Bypass -File C:\caminho\gaiapus-integracao\run_daily.ps1`.
- **Linux:** `crontab -e` e `30 6 * * * cd /opt/gaiapus-integracao && ./run_daily.sh`.

Os logs ficam em `logs/`. O arquivo `dist/snapshots_live/latest.json` só avança quando o instantâneo do dia termina sem erro de código. Falhas de uma fonte externa em um município viram `inputs_incomplete` naquele município, e a execução seguinte tenta de novo.

### 4.3 Limites das fontes gratuitas

| Fonte | Limite | Efeito |
|---|---|---|
| Open-Meteo (radiação ERA5) | ~10 mil chamadas/dia no plano gratuito | A carga inicial de todos os municípios pode levar 2 dias. Nesse período, parte dos municípios sai como `inputs_incomplete` |
| NASA GES DISC | token pessoal | Sem token, não há chuva ao vivo |
| NASA POWER, IBGE SIDRA | sem chave | Evite rodar o job várias vezes por dia |

Não use várias contas para driblar a cota do Open-Meteo. Para carga maior, a alternativa legítima é baixar o ERA5 pelo Copernicus CDS (gratuito para uso não comercial).

---

## 5. Usando no backend

### 5.1 Exemplo pronto (FastAPI)

```bash
uvicorn example_api:app --reload
# http://127.0.0.1:8000/docs
```

| Rota | O que faz |
|---|---|
| `GET /v1/catalog` | Catálogo completo (culturas, municípios, cortes, modelos, erros, teste) |
| `GET /v1/yield?crop=soy&municipality=5107925` | Estimativa do instantâneo do dia |
| `POST /v1/yield` `{"crop":"soy","municipality":"5107925","sowing_date":"2026-10-20"}` | Estimativa na hora, com semeadura informada |
| `GET /v1/water?crop=soy&municipality=5107925` | Projeção hídrica do dia (municípios do MVP) |
| `GET /v1/field-shift/sorriso` | Caso Field Shift |
| `GET /health` | Data e situação do último instantâneo |

### 5.2 Uso direto em Python

```python
import json
from datetime import date
from pathlib import Path
from data.serving.providers import Live
from data.serving.yield_service import estimate_yield

catalog = json.loads(Path('dist/gaiapus-models-v7/catalog.json').read_text(encoding='utf-8'))
provider = Live('data/cache/live', mode='operational')
answer = estimate_yield('soy', '5107925', date.today(), catalog=catalog,
                        models_dir=Path('dist/gaiapus-models-v7/models'),
                        get_weather=provider.weather, get_history=provider.history, mode='operational')
```

### 5.3 Códigos de cultura

`soy`, `maize2` (milho 2ª safra), `maize1` (milho 1ª safra), `wheat`, `rice`, `bean1` (feijão 1ª safra), `bean2` (feijão 2ª safra), `coffee_arabica`, `coffee_conilon` e `coffee_total_mg`. Município: código IBGE de 7 dígitos, como texto.

---

## 6. As respostas (contratos)

Os esquemas estão em `contracts/` e há exemplos de cada caso em `contracts/examples/`.

### 6.1 Estimativa de rendimento (`yield-estimate.schema.json`, versão 1.2.0)

A 1.2.0 só acrescenta `model.scientific_status` e `model.historical_reference`; respostas 1.1.0 continuam válidas e nenhum modelo mudou.

| Campo | Significado | Como mostrar |
|---|---|---|
| `status` | `ready`, `before_first_cutoff`, `outside_season`, `inputs_incomplete` | ver tabela 6.2 |
| `estimate_kg_ha` | estimativa municipal na colheita | número principal |
| `harvest_year`, `sowing_date`, `sowing_basis` | safra e semeadura usada; `sowing_basis` diz se veio do calendário, do produtor ou do início do ano do café | "Semeadura considerada: 05/10 (calendário estadual)" |
| `cutoff_days_used`, `milestone` | até que dia após a semeadura (ou marco do café) o modelo usou dados | "Dados até o dia 30 da safra" |
| `observed_through` | último dia de dado usado | "Dados até 03/11/2026" |
| `validation.mae_kg_ha`, `bias_kg_ha` | erro médio e viés na validação 2019–2021, nesse corte | "Erro típico ± 158 kg/ha" |
| `validation.out_of_training_municipalities` | erro em municípios que não entraram no treino | detalhe |
| `validation.test_2022_2024` | resultado do teste único: `confirmed`, `not_confirmed` ou `worse_than_history` | selo de confiança |
| `model.name`, `model.scope` | modelo usado e escopo (`national` ou `MG+MT`) | detalhe técnico |
| `model.version` | versão do modelo servido (`research_v7`) | sempre visível: "Modelo research_v7" |
| `model.scientific_status` | resultado do teste único 2022–2024 do modelo servido contra o histórico: `confirmed_on_2022_2024_test`, `not_confirmed_on_2022_2024_test`, `worse_than_history_on_2022_2024_test`, `not_tested` | sempre visível ao lado do número (ver 6.1.1) |
| `model.historical_reference` | referência "histórico" (Ridge de tendência + rendimentos PAM publicados) e os MAEs do teste: histórico × modelo servido | "No teste 2022–24: modelo 454 kg/ha × histórico 426 kg/ha" |
| `coverage` | dias de dado esperados e presentes, fontes e modo | detalhe |
| `warnings` | avisos em texto: retrospectivo, entrada fora da faixa do treino, teste não confirmado, semeadura informada | lista |
| `operational_release` | sempre `not_released` | rótulo "estimativa experimental" |

#### 6.1.1 Como exibir versão, referência e status científico

- Mostre sempre `model.version` e um selo derivado de `model.scientific_status`:
  - `confirmed_on_2022_2024_test` → "Superou o histórico no teste 2022–24";
  - `not_confirmed_on_2022_2024_test` → "Não superou o histórico no teste 2022–24";
  - `worse_than_history_on_2022_2024_test` → "Pior que o histórico no teste 2022–24" (destaque);
  - `not_tested` → "Sem teste independente".
- Nenhum selo significa "validado para operação": `operational_release` é sempre `not_released`. Use "estimativa experimental".
- Casos que pedem destaque: **café arábica** (pior que o histórico), **café total MG** e **soja MG/MT regional** (não superaram o
  histórico). Exiba junto o MAE do histórico de `model.historical_reference`.
- Os erros são de **rendimento municipal** (IBGE PAM); não os apresente como precisão de talhão ou fazenda.
- A rodada v8 é experimental e não é servida; não há campo v8 nesta API.

### 6.2 Status

| `status` / erro | Significado | Mensagem sugerida |
|---|---|---|
| `ready` | estimativa disponível | mostrar número, erro e avisos |
| `before_first_cutoff` | menos de 30 dias de safra com dados | "Disponível a partir do 30º dia após a semeadura" |
| `outside_season` | a cultura não está em safra nesta data | "Fora da safra desta cultura" |
| `inputs_incomplete` | faltou dado de clima ou uma fonte falhou hoje | "Dados do dia incompletos; tente amanhã" |
| erro `invalid_crop` (HTTP 422) | cultura desconhecida | — |
| erro `invalid_municipality_code` (HTTP 422) | código não tem 7 dígitos | — |
| erro `municipality_not_covered` (HTTP 404) | município sem modelo para a cultura | "Ainda não cobrimos este município para esta cultura" |

### 6.3 Projeção hídrica (`water-projection.schema.json`)

`days[]` traz, para cada dia: chuva e ET0 previstas, água no solo (`storage_end_mm`, `available_water_fraction`), déficit, `ks` e `stress`. O campo `validated_lead` é `false` depois do 7º dia, porque esses dias são experimentais. `reference_days[]` traz a mesma simulação com a climatologia, para comparação. `forecast.issued_at` e `forecast.available_at` informam quando a previsão foi emitida. `yield_effect` é sempre `null`.

### 6.4 Field Shift (`field-shift.schema.json`)

`contracts/examples/field-shift-sorriso.json` traz os dois cenários, as médias de 6 anos, a sensibilidade e a série diária do último ano. Todos os indicadores são **simulados** (`simulated: true`).

---

## 7. Qual modelo é usado e quão bom ele é

MAE em kg/ha. "Histórico" é o modelo de referência que usa só os rendimentos publicados.

| Cultura | Modelo | Validação 2019–21 | Teste 2022–24 | Histórico no teste | Teste |
|---|---|---:|---:|---:|---|
| Soja (MG e MT) | MLP regional | 174 | 253 | 248 | não confirmado (diferença não demonstrada) |
| Soja (demais UFs) | Ridge | 386 | 582 | 714 | confirmado |
| Milho 2ª safra | Ridge | 1.147 | 974 | 1.039 | confirmado |
| Milho 1ª safra | Referência (média dos anos anteriores) | 1.185 | 1.376 | 1.488 | confirmado |
| Trigo | Boosting | 491 | 569 | 644 | confirmado |
| Arroz | Boosting | 407 | 412 | 428 | confirmado |
| Feijão 1ª safra | Referência | 284 | 305 | 322 | confirmado |
| Feijão 2ª safra | Boosting | 304 | 309 | 311 | não confirmado (diferença não demonstrada) |
| Café arábica | Boosting | 263 | 454 | 426 | **pior que o histórico** |
| Café conilon | Referência (última safra) | 616 | 398 | 888 | confirmado |
| Café total MG | Boosting | 292 | 361 | 350 | não confirmado (diferença não demonstrada) |

Os modelos foram escolhidos antes do teste e não foram trocados depois dele. Quando o teste não confirmou, a resposta avisa. Para o café arábica, recomendamos mostrar a estimativa com destaque para o aviso ou exibir junto o rendimento da última safra publicada.

---

## 8. Fontes de dados e atraso

| Variável | Fonte | Atraso típico |
|---|---|---|
| Temperatura, umidade, vento, orvalho, pressão | NASA POWER | 2 dias |
| Chuva | NASA IMERG Late V07 (GES DISC) | 1 dia |
| Radiação | NASA POWER (CERES); ERA5 (Open-Meteo) nos dias recentes | 6 dias |
| Rendimentos publicados | IBGE PAM (SIDRA) | anual; usa o ano da safra − 2 |
| Previsão do tempo (projeção hídrica) | ECMWF IFS 0,25° via Open-Meteo | emissão 4×/dia |

A estimativa usa dados até **hoje − 6 dias**. No treino, a chuva e a radiação vieram do IMERG Final e do CERES, que chegam com 2–3 meses de atraso. A troca para as fontes ao vivo foi medida em soja e milho 2ª: o erro mudou de 181,8 para 180,6 (soja) e de 1.431 para 1.445 (milho).

---

## 9. Problemas comuns

| Sintoma | Causa provável e solução |
|---|---|
| `HTTPError 401` no IMERG | token ausente, expirado ou sem autorizar o GES DISC (seção 3.1) |
| `LookupError: IMERG Late ... not published yet` | o dia ainda não foi publicado; normal para "ontem" de madrugada |
| muitos `inputs_incomplete` na primeira semana | cota do Open-Meteo na carga inicial; o job diário completa sozinho |
| `HTTPError 429` | limite de requisições; reduza `--workers` ou espere |
| `ModuleNotFoundError: data` | rode a partir da pasta do pacote ou defina `PYTHONPATH` para ela |
| estimativa com aviso "outside the training range" | o clima do ano está fora do que o modelo viu; mostre com cautela |

---

## 10. O que não está incluído

- Os dados de pesquisa e o código de treino. Para retreinar, use o repositório `LeandroJerse/Gaiapus_nasa`; o treino exige GPU com CUDA.
- Rendimento de talhão: as estimativas são municipais.
- Liberação operacional: depende de comprovar as datas históricas de publicação dos dados.

Dúvidas sobre dados e modelos: Leandro (time de dados).
