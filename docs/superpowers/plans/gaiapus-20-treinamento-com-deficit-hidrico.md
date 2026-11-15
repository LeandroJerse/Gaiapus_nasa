# Gaiapus — treinamento com déficit hídrico por fase, v3

Entrega de 14/11/2026. Indicadores físicos integrados a modelos de produtividade **municipal** de soja e milho de segunda safra, nos oito municípios MG/MT da base. Foram treinados 28 artefatos e comparados nos mesmos exemplos. **Os resultados são mistos; nenhum modelo foi promovido a produção.**

O corte de observações futuras está implementado e testado. A reconstrução da informação realmente publicada em cada data histórica **não está resolvida**: o POWER/IMERG Final disponível é retrospectivo. Esta distinção impede apresentar os números abaixo como acurácia de previsão em tempo real.

## 1. Implementação e reprodução

- [Construtor de atributos](../../../data/ingest/hydro_training_features.py): calcula apenas o prefixo observado, sem exigir o restante da safra.
- [Treinamento e comparação](../../../data/ingest/train_hydro_v3.py): verifica hashes de origem, separa conjuntos, ajusta pipelines e salva métricas/modelos.
- [Testes de isolamento](../../../tests/test_hydro_training.py): perturbações no futuro, lacunas, bloqueio de publicação, isolamento temporal/espacial e equivalência com o motor físico.
- [Motor hídrico anterior](gaiapus-19-balanco-hidrico-et0.md): equações, parâmetros e limitações agronômicas permanecem válidos.

```powershell
python -m unittest discover -s tests
python -m data.ingest.train_hydro_v3
python -m data.ingest.freeze_training_v3
```

Executar na raiz; alternativamente, usar `uv run python -m ...` em ambiente com as dependências. Ambiente efetivamente usado: Python 3.13, scikit-learn 1.7.2, pandas 2.3.3, NumPy 2.3.3 e joblib. Nenhum pacote novo foi instalado. O aviso do joblib sobre ausência do utilitário Windows `wmic` apenas fez usar a contagem de núcleos lógicos; a execução terminou com código zero.

As saídas estão em `data/processed/training_v3/` e `data/models/research_v3/`. Não se sobrescreveram v1/v2 ou resultados do balanço. Reexecutar atualiza apenas v3; versionar a pasta antes de mudar o protocolo.

## 2. Atributos e significado do horizonte

Para cada amostra municipal/cultura/ano, calculam-se atributos **até o 60º ou 90º dia inclusivo** do ciclo hipotético. Primeiro dia = plantio; corte = plantio + horizonte − 1. Modelos são separados por horizonte e cultura. Os dois horizontes não duplicam a quantidade de safras independentes.

Soja: plantio hipotético 05/11 em MG e 05/10 em MT do ano anterior à colheita. Milho: 05/01 do ano de colheita. Continuam sendo ciclos independentes, não uma sucessão soja→milho validada, nem plantios observados.

| Grupo | Atributos candidatos |
|---|---|
| Tendência | Ano de colheita, conhecido na decisão |
| Clima | Chuva acumulada, dias <1 mm, maior sequência seca, médias Tmin/Tmax, dias Tmax>35 °C, radiação acumulada, umidade relativa e vento médios |
| Hídrico por fase | Dias observados, déficit ETc−ETa acumulado, ETa/ETc e dias com Ks<1 |
| Reserva hídrica | Armazenamento disponível no último dia do prefixo |

Usa CAD AD4, água inicial de 70% e Kc/fases do motor anterior. Não foram escolhidos CAD, plantio ou parâmetros para minimizar erro contra a colheita. O solo é hipótese fixa e não análise medida de cada município. Chuva e ET0 são do ponto v1 completo; não se mistura clima ponderado v2 com ET0 pontual.

Fase ainda não iniciada recebe contagem zero e **métricas ausentes**, não déficit observado igual a zero. Fase em andamento contém somente seus dias já transcorridos. No horizonte 60, soja tem 10 dias reprodutivos; milho ainda não tem dias reprodutivos. No horizonte 90, soja tem 40 e milho 30 dias reprodutivos. Maturação não é observada em nenhum dos dois horizontes.

Colunas constantes ou inteiramente ausentes são retiradas **usando apenas o conjunto de treino**. Assim, o modelo não recebe um valor inventado para a maturação futura. Os contratos por artefato registram exatamente os atributos usados e retirados. A imputação dos demais campos e o escalonamento também são ajustados apenas no treino.

Não foram usados como entradas: rendimento do ano corrente, rendimento histórico com publicação não reconstruída, município como identificador, resumos da safra inteira, déficit futuro ou cenário artificial de seca. Os cenários −50%/−100% de chuva não viraram novas safras com rótulos de rendimento copiados.

## 3. Proteções contra vazamento

### Tempo das observações

O construtor recebe o prefixo e verifica todos os dias esperados, inclusive duplicatas. Uma série truncada exatamente no corte basta para calcular os atributos. O teste modifica chuva/ET0 posteriores ao corte para valores extremos e comprova que os atributos permanecem iguais. Outro teste confere armazenamento e déficit contra o prefixo da simulação física completa, para soja/milho e ambos os horizontes.

Não há interpolação bidirecional, preenchimento com dados futuros, uso do fim do ciclo nem estatísticas de normalização estimadas na validação. A reserva de solo evolui sequencialmente desde o plantio.

### Tempo de publicação e versão dos dados

IMERG Final tem latência aproximada de **3,5 meses**, e o POWER combina Final/Late conforme a proximidade do presente. Além disso, o Final usa ajustes mensais: cortar datas de observação não remove a informação retrospectiva embutida no próprio produto. Ver [documentação POWER](https://power.larc.nasa.gov/docs/methodology/data/sources_meteorology/) e [diferenças entre execuções IMERG](https://gpm.nasa.gov/resources/faq/what-are-differences-between-imerg-early-late-and-final-runs-and-which-should-be-used).

Por isso, `publication_time_leakage_resolved=false` e `operational_approved=false`. Não alegar que esta base estava disponível no 60º/90º dia das safras históricas. A auditoria cobre causalidade das operações sobre as linhas, não causalidade interna do produto reprocessado.

`features_at_cutoff(..., operational=True)` exige `available_at` por observação e recusa ausência ou publicação posterior à decisão. A base atual não fornece essas datas e é rejeitada. Esse campo deve vir de um catálogo confiável de versões/ingestão, nunca ser preenchido artificialmente com a data observada. Antes de servir previsões, validar também o manifesto do modelo, a versão de cada variável e a disponibilidade dos rótulos do treino. A função de atributos não substitui essas verificações do serviço.

### Divisão do treino e rótulos

| Conjunto | Anos | Uso |
|---|---|---|
| Ajuste | 2002–2016 | Somente seis municípios; 90 amostras soja e 78 milho por horizonte |
| Embargo | 2017–2018 | Não extraído nem ajustado nesta rodada |
| Validação temporal | 2019–2021 | 18 exemplos por cultura/horizonte, nos seis municípios |
| Validação espaço-temporal | 2019–2021 | 6 exemplos: Uberaba e Sinop, ausentes do ajuste |
| Teste reservado | 2022–2024 | Sem extração de atributos, ajuste, predição ou avaliação nesta rodada |

O arquivo original é lido para identificar anos, mas os valores de rendimento dos anos reservados não são copiados nem usados. Histórico antigo dos dois municípios reservados permanece identificado como `unused_spatial_history` e não entra no ajuste. São 271 amostras únicas elegíveis no arquivo v3, representadas em 542 linhas pelos dois horizontes; isso inclui o histórico espacial não usado.

O embargo evita usar como rótulos de ajuste safras imediatamente anteriores a decisões ainda no ano anterior à colheita. É conservador, mas **não reconstrói as versões e datas oficiais de publicação do IBGE**. Essa continua sendo condição para teste operacional. Não houve seleção de hiperparâmetros, ajuste de parâmetros físicos ou escolha automática de campeão na validação. Os conjuntos 2019–2021 já foram examinados em estudos anteriores e não são teste final independente.

## 4. Comparação justa: mesma informação, com e sem indicadores

Quatro grupos: `trend`, `weather`, `hydro`, `weather_hydro`. Ridge com alpha=10; HistGradientBoosting com 100 iterações, 7 folhas, mínimo 15 amostras/folha, regularização L2=10, seed=42 e early stopping desligado. Tendência usa apenas Ridge. O experimento compara representações, não busca redes neurais ou o melhor hiperparâmetro.

MAE em kg/ha; menor é melhor. Cada célula mostra **clima → clima+hídrico**. As mesmas amostras são usadas em cada par.

| Cultura | Horizonte | Modelo | Validação temporal (n=18) | Espaço-temporal (n=6) |
|---|---:|---|---:|---:|
| Soja | 60 | Ridge | 381,39 → 451,63 | 423,70 → 484,17 |
| Soja | 60 | HGB | 506,13 → 475,26 | 582,66 → 558,53 |
| Soja | 90 | Ridge | 350,53 → 414,92 | 354,82 → 421,07 |
| Soja | 90 | HGB | 507,11 → 530,74 | 512,96 → 470,00 |
| Milho 2ª | 60 | Ridge | 1.331,30 → 1.355,47 | 1.356,76 → 1.372,33 |
| Milho 2ª | 60 | HGB | 1.552,46 → 1.531,72 | 1.523,41 → 1.533,23 |
| Milho 2ª | 90 | Ridge | 1.244,69 → 1.313,12 | 1.316,99 → 1.287,75 |
| Milho 2ª | 90 | HGB | 1.513,44 → 1.485,15 | 1.297,62 → 1.119,02 |

**Soja:** indicadores pioram Ridge em todos os recortes; HGB melhora ambos no dia 60, mas no dia 90 piora a validação temporal. **Milho:** HGB no dia 90 reduz MAE temporal em 1,87% e espaço-temporal em 13,76%. Isso não demonstra ganho generalizável: são poucos exemplos, e o modelo com hídrico continua pior que outras combinações em alguns recortes.

### O agregado pode esconder piora em MG

Exemplo HGB, horizonte 90:

| Cultura / validação | MG: clima → +hídrico | MT: clima → +hídrico |
|---|---:|---:|
| Soja / temporal | 590,12 → 638,11 | 424,10 → 423,37 |
| Soja / espaço-temporal | 639,14 → 574,93 | 386,78 → 365,08 |
| Milho / temporal | 2.275,48 → 2.292,77 | 751,39 → 677,52 |
| Milho / espaço-temporal | 2.064,81 → 1.869,67 | 530,43 → 368,36 |

Temporal por estado tem nove exemplos; espaço-temporal tem apenas três por estado. Não foram calculados intervalos de confiança nem alegada significância estatística. Os CSVs também guardam RMSE, viés e predições individuais para inspeção.

## 5. Comparação com os modelos anteriores

Os resultados v1/v2 foram preservados e copiados para `previous_models_reference.csv`, com marcador de comparação contextual. Referência anterior: [guia 18](gaiapus-18-resultados-v2.md).

| Modelo anterior | Soja: MAE temporal / espacial | Milho: MAE temporal / espacial |
|---|---:|---:|
| Ponto v1, Ridge | 189,83 / 294,99 | 1.262,82 / 1.179,55 |
| Ponderado v2, Ridge | 191,77 / 305,07 | 1.321,71 / 1.113,53 |
| Ponderado v2, HGB | 174,64 / 300,12 | 1.548,61 / 1.267,29 |

Esses números **não medem isoladamente o efeito do hídrico**. Os antigos treinaram até 2018, usaram rendimento histórico defasado e janelas fixas até 31/01 (soja) e 30/04 (milho). O v3 encerra treino em 2016, retira o histórico de rendimento e usa 60/90 dias desde outro início de ciclo. Portanto, piora contra v1/v2 pode refletir menos informação, corte anterior e menor treino. A evidência sobre acrescentar indicadores está nos pares internos da seção 4.

Decisão: preservar todos como pesquisa e não substituir o modelo anterior automaticamente. O valor atual do balanço é fornecer atributos físicos auditáveis e cenários explicáveis; o ganho preditivo ainda não é consistente.

## 6. Artefatos e consumo pela equipe

| Arquivo em `data/processed/training_v3/` | Conteúdo |
|---|---|
| research_samples.csv | 542 linhas, 271 safras distintas × dois horizontes; fases futuras ausentes |
| cutoff_audit.csv | Última observação, corte, dias usados e indisponibilidade operacional |
| model_comparison.csv | 56 linhas de métricas das quatro ablações |
| model_comparison_by_state.csv | 112 métricas por MG/MT |
| paired_hydro_effect.csv | 16 comparações de inclusão dos atributos hídricos |
| validation_predictions.csv | 672 predições de validação com valores observados |
| previous_models_reference.csv | Métricas v1/v2, com aviso de desenho diferente |
| validation.json | Contagens e estado das verificações |
| delivery_manifest.json | Inventário e hashes |

`data/models/research_v3/` contém 28 arquivos joblib e `manifest.json` com bibliotecas, hash dos dados, parâmetros de divisão, lista de atributos por modelo e IDs do ajuste. Carregar somente artefatos locais confiáveis: joblib é formato executável, não contrato para upload de modelos por usuários.

Backend futuro: carregar manifesto e modelo compatíveis com cultura/horizonte, aplicar exatamente o contrato de atributos e exigir versão válida dos dados. **Recusar publicação operacional enquanto `operational_approved=false`.** Não produzir valor de rendimento de talhão a partir de rótulo municipal, nem interpretar sensibilidade do regressor como efeito causal de irrigação.

Frontend futuro: mostrar horizonte, datas observadas, fase em andamento, cobertura e rótulo de pesquisa. Fases futuras ficam como “ainda não observadas”. Não desenhar a safra futura como observação. Resultados de cenários físicos e estimativas estatísticas devem indicar suas respectivas hipóteses.

## 7. Próximos passos necessários

1. Registrar `observation_time`, `published_at/available_at`, `ingested_at`, execução/versão e hash em cada fonte. Coletar produtos de baixa latência e manter suas versões; não atribuir artificialmente ao Final o horário do Late.
2. Construir histórico equivalente de IMERG Late/Early e meteorologia realmente disponível, com suas latências. Reavaliar/calibrar a diferença de distribuição frente ao Final; não trocar o produto apenas na inferência.
3. Reconstruir datas/versões de publicação dos rótulos IBGE; voltar a incluir histórico de rendimento somente quando sua disponibilidade for auditável. Usar splits por data de decisão e de publicação, não só ano de colheita.
4. Obter plantio, cultivar, manejo, solo e rendimento de talhões para calibrar CAD/raízes/Kc. Investigar especialmente o erro de milho em MG. Ampliar safras/municípios antes de redes neurais de alta capacidade.
5. Pré-registrar o próximo experimento. Manter 2022–2024 fechado até congelar método e contrato temporal; então avaliar uma única vez e reportar também os resultados por estado/município.

Os 30 testes da suíte passaram nesta entrega. Isso sustenta a implementação e os controles descritos; não resolve a falta de versões históricas nem valida produtividade em campo.
