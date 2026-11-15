# Execução — IMERG Early/Late e disponibilidade

- [x] Consultar CMR NASA para Early/Late/Final; arquivar metadados e URLs oficiais de 2016–2021.
- [x] Baixar piloto 01/01/2021 após autorização do usuário no navegador; extrair 24 células MG/MT, preservar tentativas HTTP 401 anteriores.
- [x] Implementar snapshots imutáveis e seleção as-of por primeira obtenção comprovada, não por latência nominal/updated.
- [x] Testar exclusão de revisões futuras e bloqueio sem rótulos/variáveis disponíveis no instante correto.
- [x] Preparar comparação Early/Late/Final e auditoria de prontidão; atualizar documentos e revisar.

Decisão: nenhuma troca nominal de produto resolve as versões históricas. CMR updated, data de produção e Last-Modified não serão convertidos em primeira publicação. O timestamp first_seen_at será gerado após download bem-sucedido e será conservador para o nosso sistema. Credenciais não serão escritas no projeto. Teste de rendimento 2022–2024 permanece fechado.

## Evidências e pendências

- 6.576 registros oficiais CMR, três NetCDF, oito municípios, 16 pares válidos. O histórico 2016–2021 não foi baixado integralmente.
- Usuário autenticou e aceitou GES DISC pessoalmente; download no Chrome, importação sem transferência de cookies/credenciais para Python.
- Falha de abertura NetCDF em caminho Unicode do Windows: arquivo existia, biblioteca C não o abria. Leitura por bytes (`memory=`) confirmou conteúdo e corrigiu extração.
- Teste de decisão de treino posterior ao ajuste falhou antes da correção; agora rejeita. Construtor hídrico com todas as dependências também passou após teste inicial falhar por ausência da função.
- Revisão independente `review_operational_imerg` encontrou dois P2: versões antigas descartadas antes do gate e CSV mutável não confrontado com conteúdo bruto. Ambos corrigidos com testes RED→GREEN (`test_all_revisions_reach_asof_selection`, `test_extracted_csv_cannot_override_receipt_or_rain`). Suíte final 38/38 e relatório real regenerado sem alteração das métricas do piloto.
- Nenhum treino operacional executado: 672 candidatos, zero elegíveis. Faltam séries completas, versões históricas e evidência temporal das demais variáveis/rótulos. Nenhuma alegação de conclusão desses itens.
