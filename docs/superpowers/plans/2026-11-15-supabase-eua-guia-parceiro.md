# Gaiapus — guia do parceiro para publicar os EUA no Supabase

**Data:** 15/11/2026. **Responsáveis:** agentes autorizados por Leandro e parceiro responsável pelo deploy/operação.

**Estado atualizado em 15/11/2026:** a publicação remota foi concluída via CLI. O lote `us-74a444b5fb7f886e` está ativo: 245 condados, 710 runs e 1.422 objetos verificados. Brasil preservado; modelos não promovidos. Não repetir a carga por presumir que o roteiro abaixo ainda está pendente. Consulte `handoff/us-v2-supabase/ESTADO.md` e `EFICIENCIA-E-OPERACAO.md` no repositório científico para evidências e configuração da API. Não foi feito deploy nem merge do frontend nesta operação.

**Atualização de autorização:** Leandro informou que o Supabase já existe e autorizou os agentes a utilizá-lo. O procedimento não precisa mais ser executado exclusivamente pelo parceiro. O destino autorizado é o projeto `kgtvvdwwqnmmjbtcmbbu`: <https://supabase.com/dashboard/project/kgtvvdwwqnmmjbtcmbbu>. Não criar projeto novo nem usar outro banco. O parceiro pode executar ou acompanhar as etapas e continua recebendo os relatórios.

**Acesso resolvido pela CLI:** após o login feito por Leandro, a CLI 2.98.2 acessou o destino correto. O conector MCP anterior não foi usado na operação. Credenciais administrativas permaneceram apenas na memória do processo de upload; não foram gravadas no código, relatórios ou `.env`.

Plano técnico associado: [Integração EUA → APP → Supabase](2026-11-15-integracao-eua-app-supabase.md).

## 0. Preparado × implantado (atualizado em 15/11/2026, depois da implementação)

Os comandos estão implementados e testados localmente. O roteiro com os comandos exatos e as saídas esperadas é `Docs/integracao-eua/EXECUCAO-PARCEIRO.md` no Gaiapus-APP, branch local `codex/integracao-eua-supabase` (sem push). **Nenhuma migração ou carga remota foi feita.**

**Quem executa no destino:** o Codex (logado na conta correta de Leandro) ou o parceiro. A sessão do Claude que implementou este trabalho tem a CLI do Supabase logada em outra organização, e por ordem de Leandro não executa nada de Supabase do Gaiapus; ela só rodou, antes dessa ordem, `supabase migration new` (cria um arquivo local) e `supabase projects list` (leitura).

| Item | Preparado e testado localmente | Aplicado no Supabase | No deploy |
|---|---|---|---|
| Contratos (métricas anuláveis, `post_harvest_no_daily_series`, `yield_result`) | sim | não se aplica | não |
| Migração aditiva `20261115163549_simulation_publication_batches.sql` | sim, em Postgres 17 isolado | **não** | não |
| API com fonte local e fonte Supabase | sim, com Storage emulado | **não testada contra Supabase real** | não |
| Ferramenta de publicação | sim, com Storage emulado e Postgres isolado | **nunca executada no destino** | não |
| Pacote e SQL do lote `us-74a444b5fb7f886e` | gerados e conferidos | **não enviados** | não |
| Tela de planejamento abrindo os EUA | **não** (reservada à outra sessão do Claude) | — | não |

### Entrega

Pasta fora do Git: `C:\Users\leand\Backups\gaiapus\entrega-eua\`

| Arquivo | Bytes | SHA-256 |
|---|---:|---|
| `gaiapus-us-v2-publication-69c23cefe48e.zip` | 153.405.598 | `1c7dd29abe7a1c71e642ccdefb62cb21526abb99b29257b7cac48611864a9d57` |
| `us-74a444b5fb7f886e.metadata.sql` | 1.877.524 | `4a052a3499f53502729f1d8d6df8d2ab75e473dc5416f4ef5ea3bf9a800e0f94` |
| `us-74a444b5fb7f886e.activate.sql` | 731 | `725403ec03802bfab62bcaf3660fe76c14d2b2f83167129ffb2eee68cc3a48a5` |
| `us-74a444b5fb7f886e.rollback.sql` | 715 | `0929057806e204d365253a0e7d9967fccbe054022baac6169a3024146a325251` |

O lote tem 245 lugares, 710 execuções com série diária e 490 resultados só de rendimento (400 de 2026 e 90 do trigo KS 2027), em 1.422 objetos e 151.873.694 bytes (151,87 MB decimais; 144,84 MiB). O número "143 MB" citado abaixo era da revisão anterior, sem os resultados só de rendimento no catálogo. Reproduzir o pacote: `python -m scripts.us_v2_package --tag 2026-11-15` no repositório científico (commit `b162721`).

### Comandos implementados

```powershell
pnpm install --frozen-lockfile
pnpm --filter @gaiapus/contracts build
node scripts/simulations/publication-tool.mjs inspect       --dir <pacote>\publication   # sem rede
node scripts/simulations/publication-tool.mjs validate      --dir <pacote>\publication   # sem rede
node scripts/simulations/publication-tool.mjs plan          --dir <pacote>\publication   # só leitura; SUPABASE_URL
node scripts/simulations/publication-tool.mjs upload        --dir <pacote>\publication   # SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
node scripts/simulations/publication-tool.mjs verify-remote --dir <pacote>\publication   # só leitura
psql "$env:DATABASE_URL" -v ON_ERROR_STOP=1 -f us-74a444b5fb7f886e.metadata.sql          # lote fica "staged"
psql "$env:DATABASE_URL" -v ON_ERROR_STOP=1 -f us-74a444b5fb7f886e.activate.sql          # ativa, uma transação
psql "$env:DATABASE_URL" -v ON_ERROR_STOP=1 -f us-74a444b5fb7f886e.rollback.sql          # desfaz a ativação
```

### Configuração implementada

- Ferramenta: `SUPABASE_URL`; `SUPABASE_SERVICE_ROLE_KEY` somente no `upload`. Nada é lido de arquivos `.env`.
- API: `SIMULATION_US_SOURCE` (`off` por padrão, `local` ou `supabase`), `SIMULATION_US_LOCAL_DIR`, `SIMULATION_CACHE_MB` (64), `SIMULATION_FETCH_TIMEOUT_MS` (10000), `SIMULATION_BATCH_TTL_SECONDS` (60). O modo `supabase` usa `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY`; a API nunca recebe a chave administrativa.
- `SCIENCE_MODE` e a seleção de modelos não mudam. Nenhum modelo foi promovido.

### Testes executados em 15/11/2026

Contratos 28, API 56, web 264 e ferramenta 9 passam. Três testes de banco em Postgres isolado passam, um deles com o lote real de 710 execuções (aplicado duas vezes, ativado e revertido). Build e verificação de contratos gerados passam. Os hashes do catálogo e do índice brasileiros fixados no APP são os mesmos de antes. O teste `supabase/tests/policies.test.mjs` já falhava antes, por depender de um contêiner ausente. Não foram rodados E2E de navegador, testes de duas contas com Auth real nem qualquer teste no projeto de destino.

### Rollback

`us-74a444b5fb7f886e.rollback.sql` retira o lote ativo dos EUA e restaura o anterior, se houver, sem apagar nada e sem tocar no Brasil. Sem banco: `SIMULATION_US_SOURCE=off` e reiniciar a API. Objetos já enviados ao bucket público continuam acessíveis por URL. A migração é aditiva e não tem script de reversão.

### O que observar na primeira execução real

Os caminhos da API de Storage, a resposta a objeto duplicado e a leitura anônima da tabela de lotes só foram testados contra um emulador. Se algum se comportar de outro jeito, pare no passo e devolva o erro, sem tokens. Antes de aplicar a migração, confira o histórico de migrações do projeto: não se sabe se as duas anteriores já foram aplicadas lá.

## 1. O que você vai colocar no ar

Você receberá os resultados já calculados dos EUA para integrá-los à mesma aplicação que atende o Brasil. Não precisa treinar os modelos nem baixar todo o histórico NASA/USDA para consultar essas publicações.

O fluxo é **base científica → treinamento/validação → geração dos cenários → exportação → Storage/Postgres → APP**. Treinar e gerar resultados são operações diferentes: os cenários já calculados de 2026/2027/2028 devem ser reutilizados quando válidos, sem novo treino apenas por causa da hospedagem. Git guarda a implementação e a rastreabilidade; Supabase guarda os produtos consultados pelos usuários. Os pesos só precisam estar disponíveis ao serviço Python se ele executar inferências novas.

| Destino | Conteúdo |
|---|---|
| GitHub Gaiapus-APP | Código do frontend/API, contratos, migrações, ferramentas, manifestos e exemplos pequenos |
| Supabase Postgres | Catálogo e cobertura, metadados das execuções, resumos, hashes, referências aos arquivos e publicação ativa |
| Supabase Storage | Arquivos de execução e séries comprimidas por cenário, aproximadamente 143 MB no lote reportado |
| Computador/arquivo de pesquisa | Brutos, matrizes de treino, checkpoints, avaliações e históricos extensos |
| Serviço Python, quando habilitado separadamente | Cálculos novos e inferência ao vivo; não é necessário para reproduzir séries prontas |

**Publicar o resultado no APP não aprova o modelo para uso operacional.** Manter os status científicos e o catálogo de modelos brasileiros.

## 2. Identifique qual Supabase você utiliza

“No seu computador” pode significar duas situações:

1. **Projeto hospedado no Supabase, administrado pelo seu computador:** o servidor continua na nuvem. O APP usa a URL desse projeto. Sua máquina não precisa ficar ligada após a publicação.
2. **Supabase local/self-hosted no seu computador:** banco, Auth e Storage dependem dessa infraestrutura. `localhost` só funciona nessa máquina; o backend hospedado e os celulares da equipe não acessam seu `localhost`. Antes do deploy compartilhado, forneça um endpoint HTTPS acessível, persistência e backup, ou migre para um projeto hospedado. Não basta copiar a URL local para o frontend.

Anote o tipo de ambiente, identificador do projeto, URL, versão da CLI, versão do Postgres, domínio do APP e responsável. Nunca coloque senhas/chaves administrativas nesse registro compartilhado.

Para a primeira passagem, use homologação. Se já houver banco com dados do Brasil ou de usuários, não recrie o projeto e não execute reset.

## 3. O que cobrar do Claude antes de executar

- [ ] PR/commit identificado do [Gaiapus-APP](https://github.com/LuksbellN/Gaiapus-APP), com testes e build aprovados.
- [ ] Migrações novas e lista ordenada das que já eram pré-requisitos.
- [ ] Ferramenta de importação testada, com modos separados de validação, upload, verificação e ativação.
- [ ] Pacote EUA completo, manifesto SHA-256 e tamanho em bytes; não apenas os três condados de exemplo.
- [ ] Guia `Docs/integracao-eua/EXECUCAO-PARCEIRO.md` com comandos reais e versões compatíveis.
- [ ] Resultado dos testes de RLS, coexistência 2027/2028, preservação do Brasil e interrupção/retomada do upload.
- [ ] Lista de configuração necessária, sem segredos, e plano de rollback.

O pacote sugerido `gaiapus-us-v2-publication-<versao>.zip` é uma **entrega a ser gerada pelo Claude**, não um arquivo criado por este documento. Deve conter catálogo, índice, manifesto, execuções/séries e resumos de rendimento necessários para 2026 e KS 2027. Modelos, credenciais e dados de agricultores ficam fora.

Como as séries já estão em gzip, compactar o ZIP novamente pode economizar pouco. A vantagem do ZIP é transferir o lote com um manifesto, não uma promessa de compressão adicional.

## 4. Preparação e backup

1. Receba o pacote por meio acordado com Leandro e o checksum por um canal confiável. Confira o SHA-256 do ZIP e depois os hashes dos arquivos extraídos.
2. Use checkout/commit de release identificado; preserve suas alterações locais e não execute reset para sincronizar o Git.
3. Leia o guia de deploy atual do APP e o guia de execução entregue pelo Claude.
4. Registre versão do APP, publicação brasileira ativa e contagem/hash do catálogo BR antes de qualquer mudança.
5. Faça backup do banco e dos objetos de Storage que já existem. Backup de Postgres e cópia dos objetos são entregas distintas; verifique que consegue recuperar ambos. Guarde uma cópia fora do mesmo disco físico.
6. Confira espaço disponível, limite de objeto, banco, tráfego e armazenamento do seu plano/servidor. Considere novas versões e backup, não apenas o tamanho de um lote.

Comandos de inspeção seguros da CLI:

```powershell
supabase --version
supabase --help
supabase db --help
supabase migration --help
```

A CLI v3 mudou o destino padrão e a confirmação de operações hospedadas. Use os comandos testados para a versão do pacote, com destino explícito; não reutilize cegamente exemplos da v2. [Mudanças oficiais da CLI](https://supabase.com/changelog/supabase-cli-v3).

## 5. Aplicar somente migrações pendentes

Há duas migrações de referência já encontradas no APP:

1. `supabase/migrations/202611140001_personal.sql`.
2. `supabase/migrations/202611150001_future_simulations.sql`.

A segunda depende de estruturas da primeira. A integração EUA deve chegar por migração aditiva posterior, quando necessária.

- Compare os arquivos com o histórico de migrações do destino.
- Se o banco já recebeu essas alterações manualmente, peça reconciliação do histórico antes de rodar novamente; não declare uma migração aplicada sem conferir seu conteúdo.
- Revise o plano das mudanças em homologação e execute somente o que falta.
- Não altere arquivos antigos já aplicados nem use `db reset` no banco com dados.
- Confira a chave de publicação: resultados de 2027 e 2028 para o mesmo local/cultura não podem se substituir.

Depois, confira a existência e as permissões de `simulation_runs`, `simulation_artifacts`, `regional_simulation_publications` e `simulation_jobs`. Não copie as séries diárias completas para uma nova tabela improvisada. [Fluxo de migrações](https://supabase.com/docs/guides/local-development/database-migrations).

## 6. Conferir buckets e permissões

Reutilize os buckets previstos na migração:

| Bucket | Uso | Quem lê | Quem escreve |
|---|---|---|---|
| `simulation-public` | Séries e resultados regionais sanitizados | Público | Ferramenta/worker autorizado |
| `simulation-private` | Simulações particulares | Usuário/equipe autorizados | Backend/worker autorizado |

Não torne todo o Storage público. Objetos de um bucket público podem ser lidos por URL, mesmo que uma tabela tenha RLS restritiva; portanto ele nunca recebe dados particulares. A retirada de um item do catálogo não revoga uma URL pública já divulgada. [Comportamento dos buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals).

Confirme:

- leitura anônima dos resultados regionais publicados;
- recusa de escrita anônima e de escrita regional por usuário comum;
- isolamento entre duas contas para propriedades e resultados privados;
- jobs privados invisíveis a terceiros;
- RLS habilitada e grants SQL coerentes.

Não desative RLS para “resolver” um erro. Os testes devem usar papéis de usuário/visitante, não apenas uma chave privilegiada que ignora as políticas. [Documentação de RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).

## 7. Importar e ativar o lote EUA

Execute os comandos equivalentes fornecidos e testados pelo Claude, nesta ordem:

1. **Validar localmente:** schemas, arquivos, SHA-256, tamanhos, IDs, datas e ausência de credenciais/dados privados. São esperados 710 runs diários e 245 lugares no lote de referência; mudanças precisam estar explicadas no manifesto.
2. **Inspecionar sem escrever:** obter o relatório de objetos novos/existentes, registros novos e escopos que serão ativados. Qualquer remoção de BR deve fazer a operação falhar.
3. **Enviar objetos:** upload para prefixo versionado US. Se interromper, retome; não marque o lote como ativo durante o envio.
4. **Verificar remotamente:** baixar e conferir todos os objetos importados, descomprimir séries e validar schema. Não considerar só o HTTP 200 ou ETag como prova de integridade.
5. **Inserir metadados:** gravar catálogo/resumos/refs como ainda não ativos, sem duplicatas.
6. **Ativar:** executar a transação preparada que torna o lote US consultável. Preservar ponteiro anterior e todas as publicações BR.
7. **Testar pela aplicação:** somente depois anunciar disponibilidade para a equipe.

A ativação deve ser uma operação explícita e separada. Uma segunda execução do importador não deve criar duplicatas nem sobrescrever objetos. O pacote não precisa ser recalculado no seu computador.

## 8. Configurar backend e frontend

Use o mesmo projeto Supabase no frontend e no backend. O operador autorizado deve configurar valores privados no ambiente de hospedagem ou no próprio ambiente local, fora do Git e de mensagens ao assistente. Reutilize autenticação segura já configurada; não solicite senha/token no chat.

| Configuração já referenciada pelo APP | Destino |
|---|---|
| `VITE_SUPABASE_URL` | Build do frontend; URL pública correta |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | Build do frontend; somente chave publicável compatível |
| `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY` | Runtime da API, conforme loader atual |
| `APP_STORAGE_SIGNING_KEY` | Segredo do backend para o mecanismo existente de registros; sincronizar com configuração privada do banco conforme o guia do APP |
| Credencial administrativa da importação | Somente processo do importador/worker; Claude documenta o nome efetivamente implementado |
| Configuração de provider/publicação | Claude documenta os nomes reais; não há uma variável universal para ativar Storage |

Não confundir `APP_STORAGE_SIGNING_KEY` com a chave administrativa do Supabase ou assinatura de URL do Storage. Não enviar chaves administrativas por campos `VITE_*`, build arguments públicos, logs ou JSON da API.

As variáveis Vite são incorporadas no build: quando mudar a URL/chave pública, reconstrua o frontend/imagem. Configure os callbacks de Auth para o domínio HTTPS efetivo se habilitar contas.

Mantenha `SCIENCE_MODE` e seleção de modelos como estavam. Trocar a fonte de arquivos para Supabase não exige ligar inferência científica ao vivo. O backend Python só entra se houver implantação separada e validada desse serviço.

O Dockerfile atual de referência é `apps/api/Dockerfile`, que compila e serve frontend/API. Siga a versão atualizada do guia de deploy; não publique apenas `vite preview` como serviço de produção.

## 9. Aceitação antes de compartilhar o link

- [ ] `/health` e endpoints públicos respondem corretamente no domínio final.
- [ ] Brasil mantém os locais, culturas e publicações anteriores, incluindo planejamento 2027/2028.
- [ ] Mapa permite Brasil e EUA, estados e condados, busca e locais sem cobertura com aviso.
- [ ] Story/Iowa e York/Nebraska abrem as combinações disponíveis.
- [ ] Sumner/Kansas 2028 abre a série; Kansas 2027 explica a ausência de balanço diário, sem inventar radiação/ET0.
- [ ] 2026 mostra resultado pós-colheita, sem botão que promete uma série diária inexistente.
- [ ] Cinco idiomas funcionam e o cursor sincroniza gráfico, painel e 3D.
- [ ] Raízes, perfil do solo e fallback sem WebGL continuam disponíveis.
- [ ] Abrir a página não transfere o pacote inteiro; apenas o catálogo/resumo e a série escolhida.
- [ ] Testes de duas contas e visitante comprovam as permissões.
- [ ] Modelos servidos, status científicos e tarefas prospectivas permanecem iguais.
- [ ] Foram registrados tamanho real, consumo de RAM e tempos de leitura com versão/hardware.

Os 400 resultados de 2026 e os 90 casos KS 2027 não contam como 490 séries diárias. São resultados com capacidades distintas. Não bloquear a aplicação inteira por essa ausência.

## 10. Se algo falhar

1. Não apague arquivos nem restaure todo o banco imediatamente.
2. Recoloque o ponteiro de publicação US anterior, usando a ferramenta entregue, ou desative a nova publicação US com motivo visível.
3. Mantenha a fonte BR anterior e, se necessário, a imagem anterior do APP.
4. Preserve objetos novos para investigação, logs sem segredos e manifesto do lote.
5. Envie ao Claude erro, operação, versão/commit e ID do lote; não envie tokens ou dumps com informações privadas.

Schema aditivo permite retorno do aplicativo sem derrubar tabelas. O rollback funcional deve ser testado em homologação antes da ativação final.

## 11. Retorno que você deve enviar à equipe

```text
APP: commit e versão da imagem
Supabase: tipo de ambiente e identificador não secreto
Migrações aplicadas: lista
Publicação EUA: versão, SHA-256 do manifesto, número de lugares/runs
Verificação remota: objetos conferidos / total
Brasil: comparação com baseline e cenários testados
Permissões: visitante, usuário A e usuário B
Desempenho: tamanho, RAM e tempos medidos
Modelos promovidos: nenhum
URL do APP: ...
Pendências: ...
```

Não marque “Supabase pronto” se somente o SQL foi aplicado: Storage, catálogo, API, permissões e leitura pela aplicação precisam estar verificados.
