# Gaiapus — modelos v8 (experimentais)

Pacote de pesquisa da rodada v8. **Não substitui** os modelos v7 usados pelo job ao vivo e pela API
(`gaiapus-integracao-v7.zip`). Use apenas para comparação, demonstração rotulada como experimental ou
para a avaliação prospectiva.

## Conteúdo

| Arquivo | Função |
|---|---|
| `models/<escopo>.json` (+ `.pt` quando MLP) | Formato `gaiapus-v8-v1`: base (Ridge de histórico, referência publicada ou média) + `shrink` × correção (Ridge, HGB ou MLP) |
| `yield_model.py` | Carregador CPU (numpy + torch), sem pickles |
| `serve_v8.py` | `estimate(scope, municipality_code, features)` com status `ready`, `invalid_scope`, `municipality_not_covered`, `invalid_cutoff`, `inputs_incomplete` e avisos de faixa |
| `cases.json` | Casos reais (Sorriso, Uberlândia, café MG, conilon ES) com a previsão esperada |
| `validate_cpu.py` | Valida hashes, casos e entradas inválidas com CUDA desabilitada |
| `manifest.json` | Escolhas congeladas, status de validação, paridade CPU×CUDA e hashes |

## Validação

```bash
cd gaiapus-models-v8
CUDA_VISIBLE_DEVICES= python -I validate_cpu.py
```

Requisitos: Python 3.11+, `numpy`, `torch` (CPU basta). Saída esperada: `"passed": true`.

## Entradas

`features` usa os mesmos nomes e unidades das coortes (kg/ha, mm, °C, MJ/m², dias). Os nomes exigidos para
cada corte saem de `YieldModel(...).required_features(horizon_days)`. As features são geradas pelo
pipeline do projeto (`data/serving/live_features.py`).

## Status — leia antes de usar

- **Não operacionalmente validado.** Os modelos foram treinados e avaliados com clima final (IMERG Final, CERES) que não estava
  disponível nas datas de decisão; a avaliação da safra 2025 é **retrospectiva, com exposição prévia documentada** à PAM 2025.
  O desempenho com as fontes do serviço ao vivo (IMERG Late, ERA5) não foi medido.
- `validation_status` = status da validação de desenvolvimento 2019–2021 (`promotable`/`experimental`); resultados 2025 e status
  científico por cultura em `docs/superpowers/plans/gaiapus-v8-resultados.md`.
- Treino: safras ≤2023. `validate_cpu.py` verifica integridade e reprodução numérica em CPU, não a qualidade preditiva.
- Promoção operacional exige confirmação prospectiva (previsões registradas até a data de decisão, com entradas disponíveis naquela data).
