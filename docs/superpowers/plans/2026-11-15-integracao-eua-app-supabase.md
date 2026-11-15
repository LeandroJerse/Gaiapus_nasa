# Gaiapus — plano de integração EUA → APP → Supabase

> **Para o Claude executor:** use `superpowers:executing-plans` para executar as tarefas abaixo e as skills Supabase/Postgres disponíveis. Coordene arquivos com a outra sessão antes de editar. Este documento é um plano; não comprova implementação, upload ou deploy.

**Objetivo:** integrar os resultados da v2 dos EUA ao Gaiapus-APP, preservando integralmente o Brasil, e entregar ao parceiro um procedimento reproduzível para publicar os artefatos no Supabase.

**Arquitetura:** o repositório científico produz publicações imutáveis; o APP consome catálogo, resumos e séries por contratos compartilhados. Postgres mantém metadados e índices de consulta; Storage guarda séries comprimidas. A consulta a cenários já calculados não depende de treinar ou executar modelos Python a cada acesso.

**Stack:** React/TypeScript, API Node existente, contratos JSON Schema/TypeScript, Supabase Postgres/Auth/Storage e gerador científico Python existente. Preserve as versões e lockfiles do projeto; não converta o gerenciador de pacotes nesta integração. Prefira Bun para novas ferramentas JS independentes e uv para ferramentas Python.

**Especificação:** decisões de Leandro em 15/11/2026, relatório `2026-11-15-gaiapus-eua-v2-resultados.md` nesta pasta e este documento. Guia do operador: [Supabase — guia para o parceiro](2026-11-15-supabase-eua-guia-parceiro.md).

## 1. Responsabilidade e limites desta entrega

**Decisão atualizada em 15/11/2026:** Leandro informou que o Supabase já está configurado e autorizou seu uso pelos agentes. Essa autorização substitui a restrição anterior de execução exclusiva pelo parceiro. O parceiro continua responsável pelo deploy/operação e recebe o guia, mas os agentes podem preparar e executar a integração aditiva no projeto Gaiapus correto, após identificá-lo e verificar compatibilidade/integridade. Não é necessário pedir novamente a mesma autorização.

**Destino informado por Leandro:** projeto `kgtvvdwwqnmmjbtcmbbu`, painel <https://supabase.com/dashboard/project/kgtvvdwwqnmmjbtcmbbu>. Esse é o destino autorizado; não usar outro banco disponível como substituto e não criar um projeto novo.

**Acesso verificado nesta sessão:** a consulta de detalhes desse projeto pela conexão Supabase disponível retornou `You do not have permission to perform this action`. Portanto, o destino está identificado, mas o schema, buckets e conteúdo remoto ainda não foram inspecionados. Reconectar a integração usando uma conta com acesso a esse projeto, ou executar pelo ambiente já autorizado do parceiro. Isso é uma pendência técnica de acesso, não falta de autorização de Leandro. Não solicitar novamente o Project ID, nem pedir senha/token no chat. Não armazenar o e-mail pessoal neste plano.

**Verificação após a reconexão informada por Leandro:** detalhes do projeto e SQL somente de leitura continuam recusados pelo conector. A integração local avançou independentemente: revisão 2 científica em `18dd005`, contratos/migração na worktree do Claude, auditoria dos 710 runs e pacote ZIP verificado. Consultar `handoff/us-v2-validation/ESTADO.md` na raiz científica para evidências e limites. Não confundir o ZIP pronto com publicação remota concluída.

**Configuração pública posteriormente fornecida:** o `.env` contém URL do destino e chave publicável. Teste direto de `GET /auth/v1/settings` retornou 200; a rota de `simulation_publication_batches` retornou 404/PGRST205. Logo, há configuração pública utilizável, mas a tabela não foi encontrada no schema cache da API e migrações/acesso administrativo continuam não verificados. Não trocar a chave pública por permissões anônimas de escrita; não registrar valores do `.env` no Git ou nas evidências.

| Responsável | Entrega |
|---|---|
| Claude científico | Corrigir/exportar publicação, preservar resultados e protocolos, fornecer manifesto e pacote de dados verificável |
| Claude do APP | Integrar contratos/API/UI, preparar leitura do Storage/Postgres, migrações aditivas e testes de regressão |
| Agente autorizado e parceiro responsável pelo Supabase | Identificar o destino correto, preservar backup/estado anterior, aplicar apenas migrações pendentes compatíveis, importar/verificar objetos e ativar o lote |
| Leandro/equipe científica | Decidir qualquer futura promoção de modelo por processo separado |

Preparar e testar localmente continua sendo parte do trabalho do Claude. Publicar no Supabase existente está autorizado, condicionado à identificação do destino e às verificações deste plano. Se faltar acesso, concluir as partes independentes e entregar a etapa remota ao parceiro com a limitação explícita. Não interpretar essa autorização como permissão para trocar modelos, apagar dados ou abrir novamente o teste 2025.

### 1.1 Treinar, gerar cenários e publicar são etapas diferentes

1. **Treinamento:** acontece no ambiente científico com a base limpa e os cortes temporais do protocolo. Produz pesos, pré-processadores, configuração, métricas e manifesto. Não usar rótulos ainda indisponíveis na emissão, nem resultados de teste para ajustar um modelo sob o mesmo protocolo.
2. **Geração de cenários:** carrega os modelos congelados e o motor hídrico; combina entradas permitidas com trajetórias climáticas. Uma safra futura não exige um treino separado só porque mudou o ano. Reutilizar o lote 2026/2027/2028 já calculado se inputs, identidade e integridade continuarem válidos.
3. **Exportação:** gera catálogo, resumo, séries e manifestos. Não altera a classificação científica dos resultados.
4. **GitHub:** recebe código, contratos, configurações sem segredos, migrações, documentação, manifestos e exemplos pequenos. Os dois repositórios continuam separados: ciência em Gaiapus_nasa e produto em Gaiapus-APP.
5. **Supabase Storage:** recebe as publicações regionais comprimidas para consumo do APP. Artefatos de modelo, quando necessários ao Python hospedado, ficam em armazenamento privado versionado ou release de artefatos verificada; não no bundle do frontend.
6. **Supabase Postgres:** recebe metadados, cobertura, resumos, proveniência, hashes, referências aos objetos e ponteiros ativos.
7. **Backend:** serve resultados prontos; cálculo novo roda em worker/serviço científico separado quando implementado. O navegador não treina modelos nem acessa brutos de pesquisa.

Não fazer um novo treinamento apenas para corrigir schema, preencher métricas de execução ou mover o lote para Supabase. Uma nova versão científica exige protocolo/avaliação próprios. Neste lote, 2026 é pós-colheita e 2027/2028 são cenários climáticos, não previsões meteorológicas diárias garantidas.

### Restrições globais

- Preservar mapa global, estados/condados dos EUA, busca e navegação do Brasil.
- Preservar histórico, comparação A/B, simulador inspirado no Lara, raízes ilustrativas, perfil do solo e planejamento brasileiro 2027/2028.
- Preservar português, inglês, espanhol, francês e alemão, acessibilidade e alternativa sem WebGL.
- Preservar contas, propriedades, registros particulares, permissões, modelos servidos e tarefas prospectivas.
- Não alterar `.env*`, `secrets/**`, `node_modules/**` ou `target/**`. Documentar configuração no Markdown; o parceiro insere segredos no ambiente dele.
- Não apagar dados, substituir publicações sob o mesmo hash, fazer force push ou misturar alterações de outra sessão no commit.
- Não migrar matrizes de treinamento, rasters globais, caches, rótulos de teste ou checkpoints para o banco do APP.
- Não considerar `published`, `ready` ou `succeeded` sinônimos de aprovação científica.

### Pontos de revisão obrigatória

1. Ausência de série diária com rendimento disponível: apresentar o resultado permitido, sem inventar animação.
2. Dois anos para o mesmo local/plano: 2027 e 2028 precisam continuar consultáveis simultaneamente.
3. Falha no Storage durante a integração: não eliminar publicações brasileiras nem anunciar sucesso parcial como cobertura completa.
4. Upload interrompido/repetido: retomar sem duplicar registros e sem reescrever artefatos publicados.
5. Acesso público versus privado: região pública não libera dados de propriedades, jobs ou simulações particulares.

## 2. Estado de referência e limites da evidência

Repositórios oficiais:

- Ciência: <https://github.com/LeandroJerse/Gaiapus_nasa>.
- Aplicação: <https://github.com/LuksbellN/Gaiapus-APP>.

O relatório e a mensagem de entrega registram 245 condados elegíveis, 1.200 casos de rendimento e 710 publicações com série diária. O último commit citado da publicação é `f9a8d7f`, então local na branch `feat/us-v2`. Reconfira o estado atual: esses nomes e números não comprovam que o remoto já recebeu o trabalho.

| Safra/escopo | Casos | Tratamento no produto |
|---|---:|---|
| EUA 2026 | 400 | Estimativa pós-colheita, anterior à divulgação do rótulo; sem série diária |
| IA/NE 2027 | 310 | Cenários pré-safra, com séries publicadas |
| KS trigo 2027 | 90 | Emissão durante a safra; rendimento disponível, balanço diário indisponível por falta de radiação observada/ET0 |
| EUA 2028 | 400 | Cenários pré-safra, com séries publicadas |

Não multiplicar 245 por todas as culturas e safras: a cobertura real é a matriz publicada, não um produto cartesiano. Locais ou culturas sem cobertura continuam pesquisáveis e mostram o motivo.

Resultados de 2025: trigo KS 563 contra 634 kg/ha do histórico ajustado; Iowa soja 120/118; Iowa milho 617/445; Nebraska soja 701/463; Nebraska milho 508/529. A cadeia pré-safra possui avaliações distintas, incluindo resultado favorável do milho de Iowa a seis meses. Não copiar a conclusão de um horizonte para outro.

O relatório descreve um ano de confirmação, exposição prévia parcial de Iowa e rótulos ausentes. Uma correção do avaliador após a abertura gerou conclusões provisórias adicionais. Manter essas qualificações. **Nenhum modelo foi promovido.**

O lote completo teve 571 segundos e pico reportado de 1.439 MB de RAM; confirmar a unidade no manifesto antes de converter para MiB. Não há medidas individuais confiáveis. O tamanho da publicação foi descrito como aproximadamente 143 MB; medir bytes exatos, MB decimais e MiB binários no pacote final.

## 3. Arquivos existentes e pontos de alteração

Caminhos relativos ao repositório indicado. Foram inspecionados em 15/11/2026; se outra sessão tiver movido arquivos, localizar o equivalente antes de editar.

| Repositório / arquivo | Responsabilidade nesta integração |
|---|---|
| Ciência: `data/ingest/us_v2_publish.py` | Exportar para o contrato final sem alterar scores ou treinamento |
| Ciência: `data/processed/us_training_v2/publish/catalog.json` e `index.json` | Catálogo e índice do lote |
| Ciência: `data/processed/us_training_v2/publish/runs/<run_id>/run.json` e `series.json.gz` | Artefatos por execução |
| Ciência: `data/processed/us_training_v2/product/` | Resumos e exemplos de rendimento, inclusive casos sem série diária |
| Ciência: `data/frozen/us_yield_scenarios_contract_v1.schema.json` | Contrato científico de rendimento |
| APP: `packages/contracts/scripts/simulation-definition.mjs` | Fonte dos schemas de planejamento; alterar aqui e regenerar tipos/schema |
| APP: `packages/contracts/src/simulation.test.ts` | Compatibilidade, métricas anuláveis e estados de cobertura |
| APP: `packages/contracts/scripts/import-publication.mjs` | Reaproveitar validação/importação; evitar incorporar todo o lote ao bundle |
| APP: `apps/api/src/modules/simulations/public.ts` | Rotas públicas existentes; adaptar provider sem quebrar respostas |
| APP: `apps/api/src/modules/simulations.test.ts` | Regressão das rotas e falhas do provider |
| APP: `apps/web/src/teammate/adapters/simulations.ts` | Cliente dos endpoints de simulação |
| APP: `apps/web/src/teammate/sim/planning-model.ts` e teste correspondente | Estado do planejamento; preservar identidades e seleção de safra |
| APP: `supabase/migrations/202611140001_personal.sql` | Dependências iniciais: identidade, funções e esquema privado |
| APP: `supabase/migrations/202611150001_future_simulations.sql` | Tabelas de execução, artefatos, publicação, jobs e buckets |
| APP: `supabase/tests/simulations.policies.test.mjs` | Testes reais das permissões das simulações |
| APP: `Docs/2026-11-14-gaiapus-supabase-setup.md` e `Docs/2026-11-15-primeiro-deploy.md` | Atualizar procedimento de destino e deploy, preservando orientações existentes |

Arquivos novos propostos no APP, somente se não houver equivalentes:

- `apps/api/src/modules/simulations/publication-store.ts`: interface de leitura independente de arquivos locais ou Supabase.
- `apps/api/src/modules/simulations/supabase-publication-store.ts`: implementação de catálogo/resumo/artefatos remotos.
- `scripts/simulations/publication-tool.ts`: inspeção, validação, upload retomável, verificação e ativação de lote.
- `scripts/simulations/publication-tool.test.ts`: casos de interrupção, idempotência e integridade.
- `Docs/integracao-eua/EXECUCAO-PARCEIRO.md`: comandos efetivamente implementados e testados, versões e checklist final.
- Nova migração aditiva, criada pelo workflow do projeto e pela CLI instalada. Não inventar uma numeração e não editar migrações já aplicadas.

## 4. Contratos e comportamento de dados

### 4.1 Métricas anuláveis

No contrato atual, os campos são `measured.seconds` e `measured.peak_python_mib`. Permitir `number >= 0 | null`. Manter obrigatórios tamanhos, quantidade de membros e dias quando efetivamente calculados.

`null` significa não medido. Não substituir por zero nem estimar RAM individual dividindo o pico do lote. O campo textual existente pode explicar a ausência; qualquer novo campo exige alteração do schema, não injeção de propriedades extras.

Separar metadados de execução do lote dos metadados de cada cenário. Preservar a possibilidade de futuras medições individuais sem obrigar recalcular o lote atual.

### 4.2 Cobertura e 2026

Adicionar ao enum o motivo proposto `post_harvest_no_daily_series` e traduções nos cinco idiomas, verificando primeiro se a outra sessão já definiu equivalente. Não criar dois nomes para o mesmo conceito.

Expor separadamente as capacidades de rendimento e série diária. O objeto de rendimento deve carregar referência a artefato/endpoint validado do produto científico. Não fabricar um `run_id` de simulação diária para preencher o catálogo de 2026.

A API/UI deve distinguir:

- resultado de rendimento existente;
- série diária disponível;
- ausência de fonte;
- cultura não suportada pelo motor;
- resultado pós-colheita;
- ausência total de publicação.

As 400 estimativas de 2026 podem ser consultadas na área de resultados/avaliação, com rótulo pós-colheita. O planejamento prioriza 2027/2028. Para KS 2027, explicar a lacuna observada; não chamar toda a emissão de pré-safra.

### 4.3 Datas, identidades e unidades

- Preservar `BR:ibge:<7 dígitos>` e `US:fips:<5 dígitos>`, incluindo zeros à esquerda. Não converter FIPS para número nem prefixar todo local com BR.
- Distinguir safra agrícola, ano de colheita e data civil do cursor.
- `issued_at` representa emissão; `as_of` representa o corte de informação, nunca uma data artificial que amplie disponibilidade. Registrar se um instante foi derivado de uma data civil e testar `as_of <= issued_at`.
- Manter data de observação, disponibilidade e coleta de fontes quando fornecidas; não inventar precisão temporal que a fonte não tem.
- Quantidades internas e legendas em kg/ha e mm conforme contrato. Conversão para bushel/acre só com fator por cultura, rastreável e testado; não necessária nesta entrega.
- Não alterar IDs científicos para corrigir apenas metadados de apresentação. Se o conteúdo/identidade mudar, criar revisão de publicação com hashes novos e referência à anterior, sem sobrescrever os bytes antigos.

## 5. Postgres, Storage e publicação consistente

### 5.1 Reutilizar o esquema

Já existem `simulation_runs`, `simulation_artifacts`, `regional_simulation_publications` e `simulation_jobs`. Reaproveitar, sem criar um banco paralelo para EUA.

Postgres: catálogo, cobertura, status, resumos compactos, versão, datas, hashes, tamanho, chave de objeto e ponteiro de publicação ativa. Storage: `run.json`, séries comprimidas e outros artefatos grandes. Não inserir as 26 trajetórias completas como JSON em cada linha do catálogo.

**Verificação importante:** a migração inspecionada tem índice único de publicação corrente por `(location_id, plan_id)`. Verificar se o `plan_id` diferencia safras ou se uma execução agrupa anos. Se não, estender a chave de publicação por escopo/safra em migração aditiva. Testar consulta simultânea de 2027 e 2028, evitando que ativar um ano esconda o outro.

Se um lote abranger vários anos, usar associação por safra com chave inequívoca; não alterar artificialmente a identidade científica só para satisfazer o índice. Registrar a decisão adotada e validar as publicações brasileiras que já agrupam anos.

### 5.2 Artefatos e acesso

A migração já prevê:

- `simulation-public`: público, destinado exclusivamente a resultados regionais sanitizados;
- `simulation-private`: privado, destinado a conteúdo particular autorizado por dono/equipe.

Manter essa separação. Um objeto em bucket público é acessível pela URL; RLS do catálogo não torna esse objeto privado. Portanto, jamais enviar dados pessoais ou geometria particular para o bucket público. Publicação regional para todos não implica acesso a jobs privados. [Documentação de buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals).

Não conceder escrita no bucket regional a navegadores. A ferramenta de publicação usa credencial administrativa exclusivamente no ambiente do operador, com escopo mínimo disponível; não a reutilizar nos handlers comuns que já usam identidade do usuário.

Chave de objeto sugerida, a implementar e documentar: `us/v2/<publication_version>/<run_id>/<sha256>/series.json.gz`. Caminhos derivados de manifesto validado, sem travessia de diretório ou URL externa arbitrária. Reservar prefixos BR e US, sem remover objetos de outro país.

Definir o transporte gzip explicitamente. Sugestão: armazenar bytes `.gz` e SHA-256 desses bytes; ferramenta de verificação baixa como binário, confere o hash e descomprime. A API pode devolver JSON já descomprimido sem herdar `Content-Encoding: gzip`. Se escolher entrega gzip direta, testar headers, descompressão automática e evitar descomprimir duas vezes.

### 5.3 Sequência transacional

1. Inspecionar e validar pacote local: schema, IDs, datas, bytes e hashes.
2. Gerar relatório do plano de importação sem mutação, separado por país/safra.
3. Enviar artefatos para prefixo imutável da nova publicação, sem sobrescrever existentes.
4. Baixar/verificar os objetos remotos; não tratar ETag como SHA-256 sem comprovação.
5. Inserir metadados como ainda não ativos, de forma idempotente.
6. Em transação Postgres, ativar somente o escopo US e registrar a publicação anterior. Nenhum leitor deve ver catálogo com arquivos faltando.
7. Executar testes de leitura pela API/UI; em falha, retornar o ponteiro US ao lote anterior e manter o Brasil ativo.

Storage e Postgres não têm uma transação única compartilhada: a ativação após a verificação é a barreira de consistência. Objetos enviados mas não ativados podem ficar órfãos temporários; registrar para manutenção futura, sem apagá-los nesta entrega.

Não usar apenas a remoção de `is_current` como mecanismo de privacidade: objetos regionais públicos e versões históricas já divulgadas continuam públicos.

## 6. Tarefas executáveis para o Claude

### Tarefa A — baseline e coordenação

- [ ] Conferir branches, diferenças locais e origem remota dos dois repositórios; identificar responsáveis por contratos, API, web, exportador e migrações.
- [ ] Registrar commits/versões de entrada e hashes do catálogo brasileiro antes da integração.
- [ ] Inventariar a publicação EUA e explicar diferenças em relação aos 710 runs/245 lugares de referência.
- [ ] Executar os testes existentes relevantes e registrar falhas prévias separadamente.
- [ ] Criar ramo isolado `codex/integracao-eua-supabase` se não houver ramo coordenado equivalente; não trocar a branch de uma sessão em execução.

**Saída:** baseline e mapa de responsabilidade, sem alteração científica.

### Tarefa B — contratos, catálogo e exportação

- [ ] Adicionar testes que falhem para métricas `null`, 2026 com rendimento sem série, KS 2027 sem ET0 e FIPS com zero inicial.
- [ ] Implementar alterações na fonte do schema, regenerar contratos e adaptar consumidores.
- [ ] Preservar os campos de proveniência do calendário; mover texto de campo não é motivo para descartar regras estruturadas existentes.
- [ ] Adaptar a exportação e validar todos os runs e capacidades, não apenas os três exemplos.
- [ ] Comparar resultados numéricos antes/depois: devem ser iguais, exceto metadados/serialização deliberadamente corrigidos.
- [ ] Commit focado, incluindo relatório de compatibilidade. Não reabrir treino, seleção ou score do teste.

**Saída:** publicação com contrato válido e cobertura honesta para os três anos.

### Tarefa C — backend e consulta sem dependência de treino

- [ ] Preservar endpoints existentes de capacidades, publicações regionais, execução e série; localizar todos os consumidores antes de modificar respostas.
- [ ] Criar interface de provider com implementações local e Supabase. A seleção do provider é configuração técnica, não promoção de modelo.
- [ ] Consultar catálogo/resumos por país/local/cultura/safra e carregar somente o artefato pedido.
- [ ] Cachear com limite de bytes/entradas e chave por versão/hash; deduplicar downloads simultâneos. Nunca manter as 710 séries descomprimidas em RAM.
- [ ] Definir timeout, limite de tamanho descomprimido, cancelamento e respostas de erro sem credenciais.
- [ ] Manter fonte brasileira local enquanto a migração dela não for verificada. Em conflito de fontes, usar precedência explícita e informar versão; não substituir silenciosamente uma emissão por outra.
- [ ] Testar falhas do Storage, arquivo corrompido, safra inexistente e Brasil acessível durante indisponibilidade US.

**Saída:** API capaz de servir publicações locais/remotas pela mesma interface. O servidor científico Python permanece necessário só para cálculo novo/inferência ao vivo já autorizada, não para reproduzir esses cenários.

### Tarefa D — frontend global e simulação

- [ ] Remover suposições de IBGE no caminho de planejamento; obter local e capacidades do catálogo.
- [ ] Permitir país → estado → município/condado → cultura → safra → trajetória, preservando deep links brasileiros existentes.
- [ ] Manter Story, York e Sumner como atalhos, sem limitar a busca aos exemplos.
- [ ] Sincronizar cursor, gráfico, painel, talhão e raízes com o mesmo membro/dia; não combinar quantis marginais para inventar uma trajetória física.
- [ ] Mostrar ausência parcial por produto; desabilitar apenas a ação sem dados, não esconder o local inteiro.
- [ ] Traduzir novos avisos e testar os cinco idiomas, teclado, celular e fallback sem WebGL.
- [ ] Testar 2026, KS 2027, Sumner 2028, Story/York disponíveis e todos os caminhos brasileiros de baseline.

**Saída:** UI com os EUA acessíveis, sem aumento artificial de capacidades ou retirada de funcionalidades.

### Tarefa E — migrações e ferramenta de transferência

- [ ] Ler schema/migration history do ambiente local de teste; criar somente migrações aditivas pendentes.
- [ ] Resolver a chave de publicação por safra e escrever teste de coexistência 2027/2028.
- [ ] Validar RLS de leitura regional, escrita restrita, proprietário privado e isolamento entre duas contas. Verificar permissões SQL além de RLS. [RLS no Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security).
- [ ] Implementar operações separadas de inspeção, validação, plano sem mutação, upload, verificação remota e ativação; documentar os comandos reais em `EXECUCAO-PARCEIRO.md`.
- [ ] Fazer upload interrompido e retomado em ambiente descartável; segunda importação deve criar zero duplicatas e não alterar BR.
- [ ] Preparar pacote de dados com manifesto de bytes/hashes, sem credenciais. Não enviar 143 MB para Git ou para `public/` do frontend.
- [ ] Atualizar o guia do parceiro com comandos testados e versão exata da CLI. Nenhum comando novo deve ser descrito como existente antes da implementação.

**Saída:** entrega que o parceiro executa sem depender dos caminhos do computador de Leandro.

### Tarefa F — validação e entrega

- [ ] Executar scripts existentes de contratos, testes web/API, typecheck e build; registrar comandos e resultados por commit.
- [ ] Rodar E2E no build de produção, não só no servidor Vite.
- [ ] Medir tamanho total e por objeto; tempo de catálogo, primeira série e cache; RSS do backend em repouso, com uma simulação e com cinco consultas distintas simultâneas. RSS não é memória exclusiva de um request.
- [ ] Comparar com baseline e registrar hardware, unidade, concorrência e cache frio/quente. Definir o limite de memória conforme o deploy do parceiro, sem presumir RAM ilimitada.
- [ ] Verificar que a carga inicial não baixa as 710 séries nem incorpora o conjunto ao bundle; catálogos devem ser compactos/paginados se necessário.
- [ ] Gerar relatório final, pacote de transferência, hashes e instruções de rollback; entregar mudanças por PR sem incluir trabalho alheio.
- [ ] Distinguir no fechamento: calculado, integrado localmente, enviado ao GitHub, aplicado no Supabase e disponível no deploy. Não declarar o último sem verificar o ambiente efetivamente publicado pelo agente autorizado ou pelo parceiro.

## 7. Regras científicas que a interface deve preservar

- O clima futuro vem de 26 anos análogos, não de uma previsão meteorológica diária para 2027/2028.
- O cenário central é um membro coerente; seco/úmido seguem regra documentada. Não misturar temperatura de um membro com chuva de outro.
- A faixa P10–P90 entre membros é dispersão de cenários. A cobertura observada de 37–52% no desenvolvimento não sustenta promessa de 80%.
- A faixa baseada em resíduos é outro produto estatístico. Exibir método e cobertura aferida sem chamá-la automaticamente de calibrada. Não recalibrar com o teste aberto de 2025 nesta integração.
- Solo de 55 mm e condição inicial de 50% são hipóteses. Estresse em 100% dos membros não demonstra probabilidade real de perda; não usar esse evento como alerta agronômico validado.
- Rendimento por condado não é medição de talhão. Raízes e crescimento visual seguem as limitações declaradas pelo motor.
- Elegibilidade de registro prospectivo e aprovação para operação são estados diferentes. A pós-colheita 2026 não prova antecipação de rendimento.
- Preservar protocolos, scores originais/corrigidos, modelos BR/v1 e seus selos. A importação deve manter o catálogo de modelos servidos intacto.

## 8. Entrega para o parceiro

Entregar o guia irmão deste plano e, após a implementação, uma pasta/pacote com:

1. Commit/PR do APP e, se necessário, commit do exportador científico.
2. Migrações novas e ordem de aplicação, identificando pré-requisitos já existentes.
3. Ferramenta de importação e comandos testados, com dependências travadas.
4. Publicação EUA completa e manifesto SHA-256; pacote fora do Git, transferido por meio acordado com o parceiro.
5. Relatório de integridade, testes, desempenho, permissões e compatibilidade brasileira.
6. Mapa de variáveis públicas/privadas, sem valores secretos, e procedimento de retorno à versão anterior.

Este plano não gera sozinho o pacote nem prova que os 192 testes relatados continuam passando. Essas verificações pertencem à execução.

## 9. Documentação externa conferida

- [Buckets públicos e privados](https://supabase.com/docs/guides/storage/buckets/fundamentals).
- [Migrações de banco](https://supabase.com/docs/guides/local-development/database-migrations).
- [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).
- [Mudanças da CLI v3](https://supabase.com/changelog/supabase-cli-v3).

Na CLI v3, os destinos e comandos mudaram em relação aos exemplos antigos. Verificar `supabase --version` e `--help`, fixar a versão e indicar explicitamente o destino no guia. Não copiar comandos v2 do documento antigo sem essa verificação. A autorização vem da decisão atualizada na seção 1 e vale para o projeto Gaiapus identificado, não para qualquer projeto acessível na conta.
