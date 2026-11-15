# Plano de execução do MVP da Gaiapus

**Meta:** demonstrar em um talhão do Triângulo Mineiro a comparação de até três sequências de cultivo com dados NASA reais, uma análise rastreável e uma leitura IoT real. **Posicionamento:** Local Impact.

**Referência:** [Proposta Gaiapus](../../../proposta-gaiapus.md).

**Arquitetura proposta:** PWA TypeScript e uma API Python para dados, recomendação e ingestão da telemetria. Firmware Rust no ESP32 se a placa e os drivers disponíveis forem compatíveis. Usar bun, uv e armazenamento simples no protótipo. As tecnologias são propostas para a equipe.

## Contratos para trabalhar em paralelo

- [ ] **Coordenação:** confirmar um município, talhão, janela e prioridades. Nomear um responsável por cada frente e um responsável pela validação agronômica.
- [ ] **App e Dados:** publicar os esquemas em `contracts/`. `POST /recommendations` recebe `field_id`, `latitude`, `longitude`, `planting_date`, `previous_crops`, `water_regime`, `soil_class`, `priority` e `constraints`. Retorna `status` (ready ou limited), `alternatives` (sequência, janelas, fatores de água e cobertura, justificativas), `missing_fields` e `evidence` (fonte, data, escala e versão).
- [ ] **API e IoT:** `POST /telemetry` autentica o dispositivo e aceita `event_id`, `device_id`, `observed_at`, `depth_cm`, `moisture_m3_m3` (nullable), `raw_moisture` (nullable), `soil_temp_c`, `battery_mv` e `quality`. Aceitar reenvio do mesmo evento com confirmação idempotente. Associar dispositivo ao talhão no servidor. `GET /fields/{field_id}/telemetry/latest` serve a última medição e seus metadados ao app.
- [ ] **Todas as frentes:** compartilhar um exemplo válido, um exemplo com lacunas e um exemplo de telemetria atrasada. Usar os exemplos para desenvolver de forma independente e rotulá-los como simulação.

## Frente A — Aplicativo

- [ ] **A1 · Cadastro** (`app/`). Criar seleção do talhão por ponto no mapa, formulário de histórico e restrições. Validar coordenadas, data e campos necessários. **Aceite:** o formulário produz um payload do contrato e sinaliza campos faltantes.
- [ ] **A2 · Diagnóstico e comparação.** Mostrar chuva, contexto de solo e fontes. Implementar comparação de até três sequências, detalhe com janela, motivos, conflitos e limitações. **Aceite:** resposta limitada explica a lacuna e não exibe ranking como se estivesse completo.
- [ ] **A3 · Telemetria e acesso offline.** Exibir umidade, temperatura, bateria, profundidade, qualidade e idade. Guardar o último diagnóstico no aparelho. **Aceite:** queda de conexão preserva a consulta salva com data e a leitura antiga é marcada como desatualizada.
- [ ] **A4 · Validação de uso.** Pedir a um produtor ou técnico para localizar o talhão, selecionar uma alternativa e explicar um dos motivos apresentados. Registrar dificuldade, tempo e sugestões.

## Frente B — IA e dados

- [ ] **B1 · Aquisição** (`data/ingest/`). Baixar POWER e IMERG para a região, preservar versão e amostra de origem. Preferir IMERG Final para o histórico. **Aceite:** duas fontes reais reproduzíveis com data e escala identificadas.
- [ ] **B2 · Transformação** (`data/features/`). Normalizar datas e unidades, remover duplicatas e aplicar flags. Calcular chuva acumulada e dias secos com limiar documentado. **Aceite:** ausência de chuva medida difere de dado ausente e lacuna relevante chega ao resultado.
- [ ] **B3 · Catálogo** (`data/catalog/`). Selecionar até quatro culturas ou coberturas, verificar ZARC da safra e documentar restrições, ciclos e benefícios esperados. **Aceite:** revisão do agrônomo e justificativa com fonte para cada regra ativa.
- [ ] **B4 · Motor e API** (`api/recommendations/`). Filtrar sequências incompatíveis e ordenar as viáveis conforme os fatores de água e cobertura. **Aceite:** cultura fora da janela não entra no ranking e prioridades diferentes podem mudar a ordem quando os critérios divergem.
- [ ] **B5 · Avaliação.** Validar três casos com técnico: condição de sequeiro, alternativa de cobertura e dados insuficientes. Congelar a versão das regras e das séries usadas. Deixar treinamento supervisionado para depois do MVP, quando houver rótulos reais.

## Frente C — IoT

- [ ] **C1 · Montagem** (`firmware/`). Testar ESP32 e sondas em bancada. Fixar unidade e profundidade, guardar medição bruta e registrar calibração. **Aceite:** amostras distinguíveis entre condição seca e úmida em teste controlado, com leitura volumétrica nula se sem calibração.
- [ ] **C2 · Coleta e fila.** Gerar um evento por amostra com identificador único e armazenar antes do envio. **Aceite:** perder Wi-Fi, reiniciar e reconectar preserva os eventos e o mesmo ID não duplica registros no servidor.
- [ ] **C3 · Integração** (`api/telemetry/`). Autenticar, validar e deduplicar. **Aceite:** uma leitura do kit físico aparece no talhão correto, com horário, profundidade, bateria e qualidade.
- [ ] **C4 · Piloto de campo.** Registrar ponto, perfil do solo e alimentação. Medir consumo e avaliar cobertura antes de recomendar LoRaWAN ou bateria solar. Documentar instalação e rotina de inspeção para parceiro local.

## Ordem de integração

| Janela sugerida | Aplicativo | IA/dados | IoT | Ponto de integração |
|---|---|---|---|---|
| Início | A1 com exemplo de contrato | B1 e B3 | C1 | Talhão, culturas candidatas e payloads acordados |
| Meio | A2 e A3 | B2 e B4 | C2 e C3 | Recomendação real e telemetria no mesmo talhão |
| Fechamento | A4 e ajustes | B5 e explicações | Demonstrar falha de rede e reconexão | Ensaio completo e registro de limitações |

**Roteiro de aceite:** cadastrar talhão → consultar histórico NASA → comparar alternativas → mostrar a fonte de uma diferença → observar leitura real do kit → repetir com rede indisponível e verificar a indicação de dado antigo. Reunir feedback do produtor e do agrônomo como evidência de potencial de impacto local.

