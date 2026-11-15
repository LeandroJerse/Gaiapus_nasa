# Gaiapus — plano de melhoria preditiva para soja MG/MT, milho safrinha e café

> Para o agente executor: use superpowers:executing-plans e acompanhe as tarefas abaixo. Leia os arquivos existentes antes de implementar. Execute com autonomia dentro deste escopo, registrando decisões, resultados negativos e limitações.

**Objetivo:** melhorar rendimento municipal e confiabilidade das entradas operacionais, comparando cada candidato com referências simples e mantendo independência entre treinamento, seleção e avaliação futura.

**Arquitetura:** preservar v7 e seus resultados congelados. Construir uma rodada v8 com manifesto, carregamento explícito por partição, features temporais rastreáveis e comparação pareada. Promover modelos por cultura e escopo somente após satisfazer os critérios registrados.

**Stack:** Python com uv, pandas/Parquet, scikit-learn e PyTorch CUDA já utilizados no projeto. Inferência exportada e verificada em CPU.

**Especificação:** pedido do usuário e protocolo detalhado neste documento. Preparado em 14/11/2026. Caminhos relativos partem da raiz ideias_nasa.

## 1. Estado conhecido e interpretação

Consultar novamente data/frozen/test_2022_2024_v7.json e metrics.csv de cada cultura. Os números abaixo vieram dos artefatos locais; não reexecute o teste para confirmá-los.

| Recorte | Escolhido v7 | MAE teste kg/ha | Comparação relevante |
| --- | --- | ---: | --- |
| Soja MG/MT | MLP site | 252,7 | Histórico ajustado 247,7; diferença +4,9; IC95% −1,2 a +10,9 |
| Milho 2ª nacional | Ridge site | 973,5 | Histórico ajustado 1039,1; média defasada simples 959,5 no escopo temporal |
| Café arábica | HGB site | 453,6 | Histórico ajustado 426,0; referência ref_cycle_mean 296,2 |
| Café total MG | HGB hydro | 360,8 | Histórico ajustado 349,6; ref_cycle_mean 306,1 |

As referências simples da tabela foram lidas no mesmo escopo temporal dos modelos. Ainda conferir pareamento de chaves e significado de cada referência no código antes de qualquer conclusão adicional.

O ganho do MLP regional não foi confirmado. O milho supera o histórico ajustado, mas a média simples também é competitiva. Café é prioridade de diagnóstico. MAE dividido pelo rendimento médio não é acurácia percentual. Rendimento municipal não valida rendimento de talhão nem resposta causal a manejo.

O teste 2022–2024 já foi aberto e influenciou a motivação desta rodada. Nenhuma decisão posterior transforma esse período novamente em teste independente. Registre esta limitação no relatório v8.

## 2. Regras temporais obrigatórias

- Preservar artefatos, hashes e resultados v7.
- Desenvolvimento, seleção de features, hiperparâmetros e calibração usarão apenas dados elegíveis até 2021, com folds temporais e espaciais.
- O registro agregado já existente de 2022–2024 serve de contexto. Nesta execução, não abrir linhas, rótulos ou previsões desse período para descobrir features, escolher modelos ou corrigir viés.
- O loader de desenvolvimento deve receber uma allowlist explícita de arquivos/partições. Evitar glob recursivo sobre todas as amostras.
- Validar no carregamento os anos, IDs e datas. Arquivo contaminado deve falhar com diagnóstico.
- Corte de observação, momento de emissão e disponibilidade da fonte são conceitos distintos. Para previsão emitida em decision_at, exigir available_at <= decision_at.
- Dado meteorológico observado após decision_at não pode entrar. Previsão futura só pode entrar se seu issued_at for anterior ou igual à decisão e estiver arquivada nessa versão.
- Data de download atual não comprova disponibilidade histórica. Latência nominal, data observada e data comprovada precisam de campos separados.
- PAM: respeitar a publicação efetiva do rendimento usado como lag. Y−2 é uma aproximação do protocolo anterior, não prova universal de disponibilidade.
- Imputação, padronização, seleção, clipping estatístico e correção de viés são ajustados exclusivamente no treino do fold.
- Não interpolar uma lacuna usando observações posteriores ao corte.
- Cortes de uma mesma safra permanecem juntos. Contar amostras independentes por município × cultura × safra.
- Features de área cultivada e irrigação devem existir na data da decisão. Máscara final de cultivo ou área efetivamente colhida conhecida depois pode causar vazamento.
- Saídas sintéticas e previsões de modelos não são rótulos de rendimento real.

### Avaliação futura independente

Primeiro auditar se 2025 ou outro conjunto posterior teve rótulos ou métricas consultados pelo desenvolvimento. Consultar proveniência e logs sem abrir os valores de um candidato a holdout.

Se houver um conjunto elegível:
1. Registrar período, unidades, exclusões e datas de publicação.
2. Separar o acesso aos rótulos de avaliação do pipeline de desenvolvimento.
3. Congelar código, dados permitidos, candidatos vencedores, critérios e hashes.
4. Executar o avaliador final conforme o protocolo registrado e reportar o resultado completo.
5. Uma falha técnica pode ser corrigida e rerodada com justificativa, sem selecionar parâmetros com base nos resultados.

Se não houver conjunto elegível, concluir a rodada com validação histórica e preparar avaliação prospectiva. Falta de holdout não impede coleta, limpeza, estudos de fontes, treino e exportação experimental.

Não existe exigência de que o teste seja exatamente 2025. A exigência é independência, qualidade e disponibilidade dos rótulos.

## 3. Mapear e reutilizar o código

Inspecionar:
- data/ingest/train_v7.py e train_v6.py.
- data/ingest/cohort_v7.py e cohort_v6.py.
- data/ingest/regional_rule_g30.py.
- data/ingest/operational_availability.py e imerg_operational.py.
- data/serving/live_features.py e source_swap_study.py.
- data/serving/catalog.py e yield_model.py.
- data/models/water_balance.py.
- data/frozen/ e os manifestos.
- tests/test_train_v7_gpu.py, test_cohort_v7.py, test_live_features.py, test_operational_availability.py e test_regional_rule_g30.py.

Criar módulos apenas onde faltar responsabilidade equivalente. Proposta:
- data/frozen/training_v8_protocol.json — protocolo registrado antes dos experimentos.
- data/ingest/cohort_v8.py — contrato temporal e construção das features novas.
- data/ingest/train_v8.py — experimentos, seleção e exportação.
- data/processed/training_v8/ — relatórios e datasets derivados locais.
- data/models/research_v8/ — artefatos experimentais.
- docs/superpowers/plans/gaiapus-v8-resultados.md — resultados e orientação de integração.

Esses nomes são propostas, não arquivos cuja existência já foi verificada. Preserve padrões existentes e evite duplicar cálculos.

## 4. Tarefa A — auditoria e protocolo

- [ ] Conferir Git, alterações em andamento, ambiente Python, versões, GPU e código em uso pelo job diário.
- [ ] Inventariar dados por cultura, município, ano, fonte, variável, unidade e disponibilidade.
- [ ] Identificar os baselines reais, inclusive como ref_cycle_mean é calculado.
- [ ] Verificar duplicatas, cortes, lags, disponibilidade da PAM e holdouts.
- [ ] Gerar manifesto do desenvolvimento permitido e checksum dos arquivos.
- [ ] Escrever protocolo v8 com candidatos, orçamento de busca e critérios desta rodada antes de calcular seus scores.
- [ ] Definir partições temporais externas e validação interna com gap coerente com publicação.
- [ ] Manter holdout espacial já definido para comparabilidade e reportar separadamente municípios conhecidos e novos.
- [ ] Criar teste que injeta deliberadamente uma linha de 2022 e verifica rejeição.
- [ ] Criar teste de indisponibilidade de lag e de previsão emitida depois da decisão.

Entrega: inventário, manifesto, protocolo e testes de vazamento. Nunca registrar valores de credenciais em logs ou manifestos.

## 5. Tarefa B — limpar e cruzar entradas de forma reproduzível

Chaves:
municipality_code, crop, harvest_year, sample_id, decision_at, horizon_days.
Para dados diários, acrescentar observation_date, source, product_version, available_at e availability_basis.

- [ ] Normalizar código IBGE como string e manter dicionário de cultura/espécie.
- [ ] Definir timezone e fronteira do dia de cada produto antes de somar chuva.
- [ ] Converter taxa de chuva para acumulado com duração real do intervalo. Não tratar mm/h como mm por intervalo.
- [ ] Documentar conversões de radiação, vento, temperatura, precipitação e produtividade.
- [ ] Manter dado ausente como ausente, com indicador e cobertura. Zero só significa valor observado zero.
- [ ] Validar valores fisicamente impossíveis e flags da fonte. Eventos extremos plausíveis são preservados.
- [ ] Registrar toda correção e exclusão com motivo e contagem.
- [ ] Testar ET0 e balanço hídrico com checagem de massa e unidades.
- [ ] Explicar capacidade de água disponível, profundidade radicular e calendário como observação ou hipótese.
- [ ] Ajustar normalização do MLP por fold, reaplicando exatamente os parâmetros na inferência.
- [ ] Preferir features agronômicas interpretáveis. “Linearizar os dados” não é requisito geral de um MLP.

Entrega: quality_report, dicionário de variáveis e amostras reproduzíveis por fonte.

## 6. Tarefa C — fontes de treino versus operação

Prioridade inicial: soja e milho 2ª nos municípios MG/MT realmente atendidos pelo MVP. Expandir café MG após validar o procedimento.

- [ ] Reutilizar o estudo existente e listar exatamente qual fonte alimenta cada feature ao vivo.
- [ ] Construir pares das mesmas localidades, datas e cortes com POWER/IMERG Final e fontes operacionais.
- [ ] Medir cobertura, lacunas, viés e erro diário/mensal de chuva e ET0, distribuição e extremos.
- [ ] Registrar diferenças entre Early, Late, Final e versões V07*, conforme catálogo realmente recebido.
- [ ] Usar dados INMET independentes e com QC nos recortes em que houver estações representativas.
- [ ] Para radiação, comparar somente períodos e unidades equivalentes. Produto de reanálise recente não comprova disponibilidade histórica em tempo real.
- [ ] Propagar cada troca até features por fase e rendimento, usando exclusivamente o desenvolvimento permitido.
- [ ] Comparar: modelo congelado com fonte substituída; modelo retreinado com fonte consistente; modelo com ajuste de fonte treinado só no fold.
- [ ] Definir e aplicar limites de cobertura previamente ao score. Registrar taxa de abstenção e municípios excluídos.
- [ ] Quando houver API com cota, usar cache, retomada e limites oficiais. Conta adicional não é estratégia para contornar cota.

Entrega: source_swap_report com efeitos nas features e nas previsões. Distinguir estudo retrospectivo de validação operacional com vintages comprovadas.

## 7. Tarefa D — soja MG/MT

Hipóteses a avaliar, uma de cada vez:
1. O conjunto de localização pode não generalizar bem entre anos.
2. O ganho climático pode variar por fase e UF.
3. Calendário fixo e clima pontual podem introduzir erro.
4. Correção residual pode ter variância maior que o sinal disponível.

Experimentos:
- [ ] Baselines obrigatórios: média histórica publicada, histórico ajustado, Ridge nacional e MLP regional congelado, todos nas mesmas chaves.
- [ ] Comparar features clima; clima+balanço; clima+balanço+localização.
- [ ] Verificar sensibilidade à retirada de latitude/longitude e à tendência temporal.
- [ ] Testar Ridge e MLP como correção do baseline de histórico escolhido dentro da validação interna.
- [ ] Testar shrinkage da correção residual: alpha em {0, 0.25, 0.5, 0.75, 1}, selecionado na validação interna. Alpha=0 representa o baseline.
- [ ] Testar calendário documentado e clima ponderado por área apenas onde os dados forem temporalmente elegíveis.
- [ ] Medir curvas de aprendizado por safras independentes e por número de municípios.
- [ ] Reportar MG e MT separadamente e agregado com 50% por UF, conforme objetivo regional.

Não escolher features com base no detalhe do teste 2022–2024. As hipóteses acima são propostas registradas antes da rodada.

## 8. Tarefa E — milho safrinha

Priorizar MG/MT e manter relatório nacional como contexto.

- [ ] Comparar com a média defasada simples, além do histórico ajustado.
- [ ] Separar diagnóstico por calendário, duração do ciclo e sinais de irrigação.
- [ ] Avaliar irrigação como atributo com proveniência. Censo 2017 e mapa de pivôs não equivalem a irrigação observada da cultura em cada safra.
- [ ] Buscar série ANA/Embrapa adequada, registrando aquisição, ano de referência e disponibilidade. Não transformar ausência de mapeamento em ausência de irrigação.
- [ ] Testar sensibilidade às datas de semeadura e a janelas ZARC elegíveis.
- [ ] Testar déficit hídrico nas fases com calendário definido antes da avaliação.
- [ ] Avaliar se o mesmo modelo atende regiões irrigadas e sequeiro. Só dividir modelos com suporte amostral e validação suficientes.
- [ ] Comparar ganho incremental de cada atributo nas mesmas safras, inclusive quando sua inclusão reduz cobertura.

Entrega: evidência sobre calendário, irrigação e água por fase; seleção baseada no ganho sobre referências fortes.

## 9. Tarefa F — café

Tratar arábica, conilon e café total MG como coortes distintas. Café total e arábica podem compartilhar registros subjacentes; não contar essas linhas como observações independentes em modelo conjunto.

- [ ] Auditar unidades e definições dos rótulos: café beneficiado, cereja, coco, massa e área colhida.
- [ ] Reconstruir as referências de última safra disponível, média bienal e média do ciclo apenas com dados publicados no corte.
- [ ] Verificar se a classe “ano de alta/baixa” usa rendimento futuro. Se derivada do rótulo, serve apenas para diagnóstico e nunca entra como feature operacional.
- [ ] Comparar calendário regional e marcos de pré-florada, pós-florada e granação com a origem das datas explicitada.
- [ ] Avaliar histórico de 2–6 anos, respeitando publicação, e interações que representem bienalidade.
- [ ] Comparar previsão direta versus correção residual da referência de ciclo.
- [ ] Testar clima de fases e da safra anterior, com limites temporais rigorosos.
- [ ] Testar poda, idade, irrigação e renovação apenas quando houver registros confiáveis suficientes. Não inventar observações.
- [ ] Para dados faltantes dessas variáveis, quantificar cobertura e mecanismo de ausência antes de usar.
- [ ] Reportar desempenho fora dos municípios do treino, principalmente conilon, cuja generalização merece atenção.
- [ ] Estimar benefício de novas fontes antes de ampliar downloads.

Entrega: diagnóstico da falha do modelo de café e benchmark robusto contra referências de ciclo. Um resultado em que a referência vence é uma conclusão válida.

## 10. Busca de modelos e GPU

Proposta de orçamento inicial, registrada antes dos scores:
- Ridge com alphas {1, 10, 100, 1000}.
- HGB com grade pequena baseada na configuração já existente.
- MLPs (32,16), (64,32), (128,64); dropout {0,0.1}; weight_decay {1e-4,1e-2}.
- Escolha de learning rate em uma grade pequena documentada antes da execução.
- Early stopping por validação interna temporal, nunca pelo holdout final.
- Cinco sementes para candidatos MLP finalistas; mesmo conjunto de sementes entre comparações.
- Treinar MLP em CUDA se disponível e registrar device, versões, duração, memória, épocas e sementes.
- Não atribuir a GPU o papel de aumentar precisão. Ridge/HGB podem permanecer em CPU.
- Avaliar perda Huber somente como experimento registrado, confrontando MAE e erro nas safras extremas.
- Reduzir ou ampliar orçamento apenas com justificativa de recursos, sem perseguir resultado de teste conhecido.

Uma MLP compartilhada entre culturas fica após estes experimentos. Exigiria validação específica e cuidado com rótulos sobrepostos de café.

## 11. Métricas e seleção

Para cada cultura, UF, ano, corte e escopo espacial:
MAE, RMSE, viés, MAE/rendimento médio, p90 do erro absoluto, número de safras, municípios e cobertura.
Agregar cortes por safra antes de agregar safras, para evitar favorecer culturas com mais cortes.

- Comparar modelos e referências nas mesmas chaves.
- Reportar cobertura original e cobertura pareada.
- Calcular diferenças pareadas e bootstrap por município, mantendo anos/cortes juntos; estratificar por UF no escopo MG/MT.
- Como os anos compartilham choques climáticos, acrescentar sensibilidade por ano e explicitar que poucos anos limitam a incerteza estimada.
- Ganho sobre a melhor referência simples é requisito de evidência para promover complexidade.
- Proposta de relevância prática: redução relativa de MAE de pelo menos 5% sobre baseline forte, IC95% da diferença abaixo de zero e nenhuma UF prioritária piorando mais de 5% em MAE.
- Os 5% são uma escolha de engenharia proposta neste plano, não padrão científico universal. Registrar esse limiar antes dos experimentos e justificar eventual alteração antes de calcular resultados.
- Avaliar antecipação: ganho disponível nos cortes iniciais e nos finais.
- Definir evento de quebra por limiar calculado somente no treino, caso implemente avaliação de risco. Métricas de classificação requerem regra fixa e quantidade de eventos suficiente.
- Intervalos preditivos exigem calibração própria com conjunto separado ou previsões fora do fold. MAE não é intervalo de previsão.
- Se o modelo não passar, manter candidato como experimental e documentar a referência vencedora.

Seleção interna e validação externa devem ser distintas. Bootstrap posterior à seleção serve como evidência condicionada ao processo e não elimina viés de seleção. O resultado independente futuro é necessário para confirmar a próxima versão.

## 12. Exportação e integração

- [ ] Exportar artefato, preprocessamento, features, unidades, cortes e cobertura.
- [ ] Registrar versão dos dados, modelo, código, treino e seleção.
- [ ] Gerar casos reais reproduzíveis para Sorriso, Uberlândia e café MG elegível.
- [ ] Executar em CPU em pasta isolada com CUDA desabilitada, usando só o pacote.
- [ ] Comparar com as previsões originais dentro de tolerância numérica documentada.
- [ ] Validar cultura inválida, município sem cobertura, corte inválido, entrada ausente e entrada fora da distribuição.
- [ ] Entrada fora da faixa histórica gera aviso calibrado; extrapolação não deve ser silenciosamente truncada sem regra validada.
- [ ] Manter API e catálogo coerentes com status experimental/retrospectivo/operacional.
- [ ] Preparar ZIP único, com hashes, README, exemplo de API e casos de teste.
- [ ] Inspecionar conteúdo descompactado para credenciais antes de distribuir.
- [ ] Não substituir automaticamente o modelo do job ao vivo apenas por ter terminado um treino.
- [ ] Registrar o que seria necessário para promover o modelo, com evidência e data.

## 13. Ordem de execução e entrega ao usuário

1. Auditoria e protocolo.
2. Fontes e qualidade no MVP.
3. Soja MG/MT.
4. Milho safrinha.
5. Café.
6. Consolidação e exportação experimental.
7. Avaliação independente quando elegível e congelada.

Ao terminar cada etapa, atualizar o relatório com hipótese, conjunto elegível, comando executado, hashes, resultados e próximo passo. Evitar reprocessar downloads íntegros; usar cache e retomada.

Relatório final:
- Quais fontes realmente melhoraram o resultado.
- Quanto cada modelo ganhou/perdeu contra cada baseline.
- Onde o modelo falha e onde se abstém.
- Resultados por UF e corte, com incerteza e cobertura.
- Status de validação das fontes ao vivo.
- Estado da avaliação independente futura.
- Artefatos exportados e testes executados.
- Limitações e hipóteses de solo, calendário e manejo.

Não prometer ganho numérico antecipadamente. A entrega completa pode concluir que uma referência simples continua sendo a melhor opção.
