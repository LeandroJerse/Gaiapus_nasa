# Gaiapus EUA — resultados executados em 15/11/2026

**Conclusão:** a primeira rodada foi coletada, treinada e avaliada. Nenhum dos cinco escopos satisfez o protocolo de confirmação de ganho. Nenhum modelo foi promovido para produção.

O Codex executou a rodada; este documento lê os artefatos salvos. Não é uma proposta de resultado. Plano e comandos: [coleta e treinamento](2026-11-15-gaiapus-eua-usda-treinamento.md).

## 1. O que foi executado

- 150 condados: 50 por estado, com dez por estado reservados para avaliação espacial.
- USDA oficial: 13,996 registros de rendimento de desenvolvimento, 1,031 de teste e 36,261 registros auxiliares. Contagens incluem registros suprimidos; eles não viram alvos zero.
- NASA POWER: 2000–2024, seis variáveis diárias, ponto interno Census por condado; não é clima ponderado por área cultivada.
- Matrizes: 4,538 linhas de seleção; 4,769 linhas de desenvolvimento final; 750 entradas de teste sem alvos. As matrizes incluem condados reservados; os filtros de fit os excluem.
- 443 exclusões de linhas por alvo ausente entre as duas matrizes de desenvolvimento; não são necessariamente safras distintas. Nenhuma exclusão por lacuna climática nesta execução.
- Cinco modelos finais por família: Ridge, HGB e MLP, além de quatro referências por escopo. Cada MLP final é a média de três sementes treinadas em CUDA.
- 45 testes passaram: 28 novos e 17 testes existentes de balanço hídrico/clima. Uma revisão independente de código encontrou e ajudou a corrigir o bloqueio após interrupção da avaliação.

## 2. Resultado primário: respeitar o escolhido na validação

MAE em kg/ha, média dos erros médios por condado. “Melhor referência no teste” é uma comparação descritiva, não uma nova seleção para servir.

| Escopo | Escolhido antes do teste | MAE nos condados conhecidos | Melhor referência no teste | MAE na reserva espacial | Pares conhecidos / espaciais | Status |
|---|---|---:|---:|---:|---|---|
| Iowa — soja | last | 427.0 | history_ridge: 334.4 | 446.2 | 101 / 25 | reference_selected |
| Iowa — milho | last | 1722.5 | mean3: 1184.3 | 1872.5 | 108 / 27 | reference_selected |
| Nebraska — soja | mlp | 634.8 | mean3: 492.3 | 806.2 | 96 / 25 | gain_not_confirmed |
| Nebraska — milho | mlp | 1482.4 | last: 1074.6 | 1880.5 | 95 / 26 | gain_not_confirmed |
| Kansas — trigo de inverno | mlp | 810.2 | mean3: 687.9 | 807.4 | 64 / 12 | gain_not_confirmed |

Todos os conjuntos espaciais ficaram abaixo dos 30 pares observados exigidos, devido à ausência/supressão de rótulos. Os dez condados foram reservados, mas nem todas as três safras possuem rendimento publicado aproveitável. Não reduzir o limiar depois de observar essa limitação.

## 3. Comparação secundária dos candidatos congelados

Esses candidatos também foram definidos antes do teste. A tabela não autoriza escolher retrospectivamente o vencedor do teste.

| Escopo | Grupo | Ridge | HGB | MLP | Último histórico | Média 3 | Tendência | Ridge histórico |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Iowa — soja | seen_counties | 336.9 | 376.5 | 313.0 | 427.0 | 373.0 | 361.3 | 334.4 |
| Iowa — soja | spatial_holdout | 446.1 | 452.6 | 439.2 | 446.2 | 390.8 | 349.0 | 463.5 |
| Iowa — milho | seen_counties | 1170.7 | 1536.6 | 2034.2 | 1722.5 | 1184.3 | 1399.3 | 2014.2 |
| Iowa — milho | spatial_holdout | 997.8 | 1665.6 | 2265.0 | 1872.5 | 1183.1 | 1157.5 | 2243.8 |
| Nebraska — soja | seen_counties | 487.1 | 599.7 | 634.8 | 508.6 | 492.3 | 772.7 | 723.5 |
| Nebraska — soja | spatial_holdout | 590.2 | 719.6 | 806.2 | 498.2 | 517.0 | 771.2 | 791.2 |
| Nebraska — milho | seen_counties | 1383.8 | 1341.4 | 1482.4 | 1074.6 | 1105.2 | 1806.8 | 1459.7 |
| Nebraska — milho | spatial_holdout | 1659.3 | 1615.6 | 1880.5 | 1386.0 | 1378.9 | 1856.0 | 1872.0 |
| Kansas — trigo de inverno | seen_counties | 559.7 | 584.4 | 810.2 | 756.4 | 687.9 | 1044.2 | 799.3 |
| Kansas — trigo de inverno | spatial_holdout | 654.1 | 614.6 | 807.4 | 866.0 | 784.3 | 1006.5 | 831.0 |

Leitura: o MLP de soja em Iowa teve bom resultado exploratório nos condados conhecidos, mas piora relativa na reserva espacial. O Ridge de milho em Iowa e os candidatos lineares/não lineares de trigo merecem estudo futuro, não promoção nesta avaliação. Nebraska mostra necessidade de investigar representatividade climática, manejo/irrigação e generalização; esta rodada não identifica uma causa única do erro.

## 4. Intervalos e cobertura

Diferença = MAE do escolhido menos MAE da referência; negativo favorece o escolhido. Bootstrap pareado por condado, 2.000 reamostragens; anos mantidos fixos. Limite ajustado controla cinco escopos × quatro referências. Esses intervalos não estimam a incerteza de todos os anos futuros.

| Escopo, conhecidos | Referência | Diferença kg/ha | IC95% | Limite superior ajustado |
|---|---|---:|---|---:|
| Iowa — soja | last | 0.0 | [0.0; 0.0] | 0.0 |
| Iowa — soja | mean3 | 54.0 | [9.5; 98.7] | 114.9 |
| Iowa — soja | trend | 65.7 | [-0.1; 130.9] | 155.5 |
| Iowa — soja | history_ridge | 92.6 | [5.1; 186.4] | 220.8 |
| Iowa — milho | last | 0.0 | [0.0; 0.0] | 0.0 |
| Iowa — milho | mean3 | 538.2 | [288.6; 813.3] | 923.0 |
| Iowa — milho | trend | 323.2 | [-101.2; 758.7] | 924.2 |
| Iowa — milho | history_ridge | -291.7 | [-515.5; -85.3] | 20.3 |
| Nebraska — soja | last | 126.2 | [53.4; 198.3] | 228.8 |
| Nebraska — soja | mean3 | 142.5 | [73.9; 211.3] | 240.8 |
| Nebraska — soja | trend | -137.9 | [-229.1; -45.7] | -12.2 |
| Nebraska — soja | history_ridge | -88.7 | [-143.3; -34.2] | -11.6 |
| Nebraska — milho | last | 407.8 | [208.8; 610.4] | 713.0 |
| Nebraska — milho | mean3 | 377.2 | [198.4; 550.2] | 618.5 |
| Nebraska — milho | trend | -324.4 | [-515.0; -134.1] | -31.5 |
| Nebraska — milho | history_ridge | 22.7 | [1.9; 42.9] | 49.6 |
| Kansas — trigo de inverno | last | 53.8 | [-76.6; 185.8] | 234.7 |
| Kansas — trigo de inverno | mean3 | 122.3 | [-7.6; 262.9] | 313.9 |
| Kansas — trigo de inverno | trend | -234.0 | [-399.3; -70.9] | -0.0 |
| Kansas — trigo de inverno | history_ridge | 10.9 | [-2.1; 22.6] | 28.3 |

| Escopo | Previsões | Rótulos observados | Rótulos ausentes |
|---|---:|---:|---:|
| Iowa — soja | 150 | 126 | 24 |
| Iowa — milho | 150 | 135 | 15 |
| Nebraska — soja | 150 | 121 | 29 |
| Nebraska — milho | 150 | 121 | 29 |
| Kansas — trigo de inverno | 150 | 76 | 74 |

Detalhes por ano, RMSE, viés, erro relativo e R² estão no JSON de score. “MAE / média do rendimento” não equivale a uma porcentagem de acurácia.

## 5. GPU e inferência CPU

| Escopo | Épocas por semente | Pico VRAM MB | Maior diferença CPU/CUDA kg/ha |
|---|---:|---:|---:|
| Iowa — soja | 32 | 65.32 | 0.00012207 |
| Iowa — milho | 1 | 65.32 | 0.00015259 |
| Nebraska — soja | 84 | 65.46 | 0.00012207 |
| Nebraska — milho | 4 | 65.31 | 0.00011063 |
| Kansas — trigo de inverno | 1 | 65.30 | 0.00003052 |

Hardware: NVIDIA GeForce RTX 4070 Ti; todos os logs de MLP final indicam `cuda:0`. Foi executado forward/backward/update real antes do treino. Os artefatos foram recarregados em processo novo, com inferência CPU reproduzindo as previsões salvas sem carregar rótulos.

Houve aviso do joblib porque `wmic` não está disponível para contar núcleos físicos no Windows; ele usou núcleos lógicos. Isso não impediu treino, paridade ou pontuação.

Ambiente observado:

```json
{
  "torch": "2.11.0+cu128",
  "numpy": "2.3.3",
  "pandas": "2.3.3",
  "scikit-learn": "1.7.2",
  "joblib": "1.5.2",
  "requests": "2.32.5",
  "pytest": "9.1.1"
}
```

## 6. Integridade, armazenamento e limites

- Hash do protocolo: `60f40fc6fd0f9feade9adfb392618923c79531bfe68bb640af812118ab7f096d`.
- Hash do score: `97689340eb5ea9769d65b98598ba2248f3d071eb124280375c61f171b45ddc81`.
- Download nacional processado em fluxo: 1.133 GB de tráfego; sem arquivo nacional persistido.
- Brutos selecionados: 37.64 MB; processados: 1.80 MB; modelos: 1.03 MB (unidades decimais).
- `test_consumed.json` foi criado antes da leitura de rótulos. Treino e score recusam nova execução desta versão depois desse marcador.
- Modelos brasileiros, front/backend e registro prospectivo não foram alterados.
- A avaliação é retrospectiva, com exposição prévia parcial a snippets públicos documentada. Snapshots revisados USDA/POWER não provam disponibilidade histórica na decisão.
- Só há um corte por cultura e calendários hipotéticos. O teste de 2024 continua usando histórico até 2020 por desenho de avaliação fixa.
- Não há prova de desempenho em talhão, de generalização para todos os EUA ou de efeito causal de manejo.

## 7. Arquivos de continuidade

- Protocolo: `data/frozen/us_training_v1_protocol.json`.
- Coleta/manifesta: `data/raw/us_training_v1/`.
- Features, congelamentos e score: `data/processed/us_training_v1/`.
- Artefatos CPU e pesos: `data/models/research_us_v1/`.
- Código: `data/ingest/us_data.py`, `us_features.py`, `us_train.py`.
- Testes: `tests/test_us_data.py`, `test_us_features.py`, `test_us_train.py`.

## 8. Como continuar

Não continuar ajustando modelos contra 2022–2024 como se esse período ainda fosse teste independente. Preservar v1; definir v2 com hipóteses explícitas, avaliação interna temporal e outra confirmação elegível. Priorizar cobertura de rótulos, representação espacial/cultivada do clima, irrigação do Nebraska e calendário do trigo. Avaliar fontes novas uma a uma. Antes de qualquer uso no app, exportar um contrato EUA próprio e manter status experimental; não reutilizar a aprovação dos modelos brasileiros.
