# Gaiapus — integração do backend com os modelos v7

Guia para o parceiro que mantém o frontend e o backend. O lado de dados entrega um job diário, um serviço Python, os modelos portáteis, o catálogo e os contratos. **Todas as estimativas são retrospectivas (validadas em 2019–2021) e não estão liberadas para uso operacional.** O app deve mostrar esse rótulo junto ao número.

## 1. Arquitetura recomendada: job diário + leitura de arquivos

```
[job diário 06:00 UTC] --> NASA POWER, IMERG Late (GES DISC), ERA5 (Open-Meteo), IBGE/SIDRA
        |  data/serving/daily_job.py  (CPU, sem GPU)
        v
dist/snapshots/<data>/yield/<cultura>.json   (uma resposta por município)
dist/snapshots/<data>/water/<município>_<cultura>.json
dist/snapshots/<data>/manifest.json  e  dist/snapshots/latest.json
        |
        v
[backend do parceiro]  lê latest.json e devolve as respostas  -->  [frontend]
```

- **Rendimento.** O job calcula a estimativa do dia para todas as culturas e municípios cobertos. O backend só lê o arquivo, sem rodar modelo nem chamar APIs externas durante a requisição.
- **Semeadura informada pelo produtor.** É o único caso que precisa de cálculo na hora. O backend chama `estimate_yield(..., sowing_date=...)`, que leva 2–3 s por município com o cache do job aquecido.
- **Projeção hídrica.** O job grava as projeções dos municípios do MVP. Também é possível chamar `data/serving/products.py` sob demanda, que leva cerca de 2 s e faz uma consulta ao Open-Meteo.
- **Field Shift.** JSON estático: `contracts/examples/field-shift-sorriso.json`.
- **Falha do job.** `latest.json` só avança quando o instantâneo está completo; se o job falhar, o backend continua servindo o último completo.

## 2. Fontes ao vivo e defasagem medidas (14/11/2026)

| Variável | Fonte ao vivo | Último dia disponível | Observação |
|---|---|---|---|
| Temperatura, umidade, vento, orvalho, pressão | NASA POWER | hoje − 2 | igual ao treino |
| Chuva | IMERG Late V07 (GES DISC, `EARTHDATA_TOKEN`) | hoje − 1 | o treino usou IMERG Final via POWER, que chega com ~2 meses de atraso; os arquivos recentes são V07C, e a validação histórica usou V07B |
| Radiação | CERES via POWER quando existe; ERA5 (Open-Meteo) nos dias recentes | hoje − 6 | CERES chega com ~2,5 meses de atraso |
| Rendimentos defasados | IBGE PAM (SIDRA) | ano de safra − 2 | hipótese de publicação |

A estimativa usa dados até **hoje − 6 dias** (`observed_through` na resposta).

**Efeito da troca de fontes**, medido nos 8 municípios do MVP em 2019–2021, nas mesmas safras e cortes:
- **Soja:** MAE de 181,8 com as fontes do treino e 180,6 com as fontes ao vivo; a estimativa muda em média 20 kg/ha.
- **Milho 2ª safra:** MAE de 1.430,9 contra 1.444,5; a estimativa muda em média 64 kg/ha.
- **Ainda não medido:** o efeito nas demais culturas e regiões, e a radiação híbrida exatamente como servida (CERES + ERA5).

## 3. O que vai no pacote (Release `models-v7-research` no GitHub)

| Item | Conteúdo |
|---|---|
| `gaiapus-models-v7.zip` | `models/` (JSON e `.pt`), `catalog.json`, `models_manifest.json` com SHA-256 e paridade, e o regulamento regional congelado |
| `gaiapus-snapshot-2021-01-20.zip` | Instantâneo completo de exemplo (10 culturas, cerca de 4.700 respostas), para desenvolver o frontend sem rodar o job |
| Código no repositório | `data/serving/` (`yield_service`, `yield_model`, `providers`, `daily_job`, `catalog`, `live_features`, `products`), `data/ingest/` (funções reutilizadas) |
| Contratos | `contracts/yield-estimate.schema.json` (1.1.0), `yield-error.schema.json`, `water-projection.schema.json`, `field-shift.schema.json` e `contracts/examples/` |

Requisitos de execução: Python 3.11+, numpy, pandas, requests, netCDF4 (IMERG Late) e torch em CPU (só para o MLP da soja em MG/MT). Não precisa de GPU. Para rodar a suíte, também jsonschema. Variável de ambiente: `EARTHDATA_TOKEN`. O Open-Meteo não exige chave, mas o plano gratuito tem limite de cerca de 10 mil chamadas por dia.

## 4. Rotas sugeridas e mapeamento

| Rota | Fonte da resposta |
|---|---|
| `GET /v1/catalog` | `catalog.json`: culturas, municípios, cortes, modelo por escopo, erro por corte e status de validação |
| `GET /v1/yield?crop=&municipality=` | resposta do município no `yield/<cultura>.json` do instantâneo atual |
| `POST /v1/yield` com `sowing_date` | `estimate_yield(crop, code, today, sowing_date=..., mode='operational')` |
| `GET /v1/water?crop=&municipality=&days=` | `water/<município>_<cultura>.json` ou `products.water_response` sob demanda |
| `GET /v1/field-shift/sorriso` | `field-shift-sorriso.json` |

Status do rendimento e o que mostrar:

| `status` | Mostrar |
|---|---|
| `ready` | kg/ha, corte ou marco, `observed_through`, MAE e viés do corte (`validation`), modelo e escopo, e os avisos |
| `before_first_cutoff` | "Disponível a partir do dia N após a semeadura" |
| `outside_season` | "Fora da safra desta cultura" |
| `inputs_incomplete` | "Dados meteorológicos incompletos hoje"; mostrar o último instantâneo válido, se houver |
| erro `invalid_crop` / `invalid_municipality_code` / `municipality_not_covered` | 422 / 422 / 404, ou cobertura vazia |

Quando `model.type = reference` (milho 1ª safra, feijão 1ª safra e café conilon), o número é a referência simples e a resposta avisa que nenhum modelo a superou. A resposta sempre traz `sowing_date` e `sowing_basis`. No café, `sowing_date` marca o início do ano produtivo (1º de junho).

## 5. Modelo usado por cultura

| Cultura | Escopo | Modelo | Formato |
|---|---|---|---|
| Soja | MG + MT | MLP com todas as fontes (regra do guia 30) | JSON + `.pt` |
| Soja | demais UFs | Ridge, todas as fontes | JSON |
| Milho 2ª safra | nacional | Ridge, todas as fontes | JSON |
| Trigo, arroz, feijão 2ª, café arábica | nacional | Gradient boosting, todas as fontes | JSON (árvores exportadas) |
| Café total MG | nacional | Gradient boosting, clima + balanço | JSON |
| Milho 1ª, feijão 1ª, café conilon | nacional | Referência simples | JSON |

Paridade conferida no empacotamento: Ridge, boosting e referências reproduzem as previsões de validação com diferença ≤ 10⁻¹² kg/ha; o MLP, recarregado em CPU, com 0,0001 kg/ha.

## 6. Como rodar

```powershell
python -m data.serving.package_models     # exporta modelos e confere paridade (requer os dados de pesquisa)
python -m data.serving.catalog            # gera catalog.json
python -m data.serving.daily_job --as-of 2021-01-20 --mode offline   # instantâneo retrospectivo, sem rede
python -m data.serving.daily_job --mode live                          # instantâneo de hoje (EARTHDATA_TOKEN)
python -m unittest discover -s tests
```

Agendar o job diário (cron ou Agendador de Tarefas) depois que os dados do dia anterior forem publicados, por exemplo às 06:00 UTC.

Tempos medidos:

| Execução | Tempo |
|---|---|
| Instantâneo offline (cerca de 4.700 respostas) | 100 s |
| Ao vivo, primeira carga da safra (IMERG Late para todo o Brasil) | ~1,5 s por dia de dado, uma vez só, compartilhado por todos os municípios |
| Ao vivo, por município com cache aquecido | ~2,5 s |
| ERA5, preenchimento inicial de ~2.800 municípios | cerca de dois dias de cota gratuita do Open-Meteo |

## 7. Pendências conhecidas

- **Rendimentos de 2022–2025 na safra atual.** As estimativas ao vivo da safra 2026/27 usam a PAM de 2023 a 2025 como entrada, e 2023 e 2024 são anos do teste reservado. Decisão pendente: avaliar o teste uma única vez antes de ligar o modo ao vivo, ou ligar sabendo que esses rótulos passam a ser lidos como entrada.
- **Validação das fontes ao vivo.** A troca de fontes só foi validada na soja e no milho 2ª do MVP.
- **Liberação operacional.** Nenhum modelo está liberado, porque as datas de publicação históricas dos insumos e dos rótulos não foram comprovadas.
