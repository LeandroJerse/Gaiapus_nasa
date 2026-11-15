# Gaiapus

Projeto NASA Space Apps para explorar produtividade municipal de soja e milho safrinha e risco hídrico em MG e MT.

- `gaiapus-mvp/` contém a demonstração do app.
- `data/ingest/` contém coleta, normalização, auditoria e treino.
- `data/models/water_balance.py` implementa o balanço hídrico.
- `tests/` contém a suíte Python. Execute `python -m unittest discover -s tests`.

- `data/serving/` monta as duas respostas do backend; os esquemas e exemplos ficam em `contracts/`.
- `data/fake/` gera telemetria sintética de sonda para testes de ingestão; nunca usar para treino.

Resultados atuais: [coleta IMERG por subset e estudo MLP](docs/superpowers/plans/gaiapus-24-coleta-subset-e-mlp.md) , [coorte v6, produtos e contratos](docs/superpowers/plans/gaiapus-26-v6-produtos-e-contratos.md) e [v7: culturas, GPU e caso Field Shift](docs/superpowers/plans/gaiapus-29-entrega-v7-culturas-e-field-shift.md).

Consulte o [roteiro operacional](docs/superpowers/plans/gaiapus-00-inicio-operacional.md), o [estado da coleta IMERG](docs/superpowers/plans/gaiapus-22-retomada-2026-11-15.md) e o [prompt de continuidade](docs/superpowers/plans/gaiapus-23-prompt-claude-coleta-e-mlp.md). Dados coletados, saídas de treino e credenciais ficam no ambiente local. Alguns scripts e testes exigem dados locais descritos nos guias.
