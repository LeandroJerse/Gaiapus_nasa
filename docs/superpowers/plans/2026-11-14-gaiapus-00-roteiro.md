# Gaiapus — roteiro de execução por etapa

Para iniciar a coleta, a limpeza, a simulação agrícola e a MLP, siga o [plano operacional de dados e IA](gaiapus-00-inicio-operacional.md). O roteiro abaixo continua valendo para organizar as três frentes.

**Estado atual:** a apresentação e o demonstrador estático são referências de produto. A ingestão e os recortes já conferidos são insumos técnicos. A equipe ainda vai criar o frontend e o backend da aplicação. Os caminhos indicados nos próximos guias são propostas para o repositório novo.

**Objetivo:** ajudar o produtor a entender o histórico do seu recorte e consultar opções agrícolas explicáveis. Uberlândia é o piloto de impacto local. Rio Verde e Barreiras demonstram repetibilidade dos dados; não validam recomendações nesses locais.

| Guia | Quem toca | Entrega que libera o próximo passo |
|---|---|---|
| [01 · Produto e contrato](2026-11-14-gaiapus-01-produto-contrato.md) | Dados + App + IoT | Fluxo de usuário, contrato v1 e amostras válidas e negativas |
| [02 · Dados e recomendação](2026-11-14-gaiapus-02-dados-recomendacao.md) | Dados/IA | Pacote publicável, relatório de qualidade e regras com fonte |
| [03 · Frontend](2026-11-14-gaiapus-03-frontend.md) | App | Telas navegáveis com amostras e depois com API |
| [04 · Backend](2026-11-14-gaiapus-04-backend.md) | App | API que entrega o pacote certo e bloqueia o inválido |
| [05 · IoT e integração](2026-11-14-gaiapus-05-iot-integracao.md) | IoT + integração | Simulador aceito pela API e roteiro de aceite ponta a ponta |

**Ordem:** façam o guia 01 juntos. Depois Dados, Frontend, Backend e IoT podem trabalhar em paralelo com as mesmas amostras. A primeira entrega em conjunto é **Uberlândia, via API, com imagem, NDVI e chuva, e um caso sem dados**. Adicionem GO e BA após passar o mesmo teste. Recomendação e telemetria entram em incrementos separados.

- [ ] Nomear um responsável por Dados/IA, App e IoT, e quem decide alterações no contrato.
- [ ] Abrir uma tarefa por entrega da tabela. Cada uma deve ter exemplo de entrada, resultado esperado e evidência de teste.
- [ ] Fechar o dia com o histórico de Uberlândia funcionando ou indicar o bloqueio exato. Se só houver um conjunto local aprovado, demonstrar com data e versão fixas.
- [ ] Antes de publicar, rodar o roteiro do guia 05 e registrar o resultado.

O [plano técnico anterior](2026-11-14-gaiapus-execucao-mvp.md) mantém o inventário, três diagramas, metodologia e cronograma de referência.
