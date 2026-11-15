# Gaiapus 04 — plano de construção do backend

**Entrega:** API real construída do zero e independente do demonstrador. Sugestão inicial: FastAPI + Pydantic, ambiente com `uv`, manifesto e arquivos derivados locais. Evoluir para banco e armazenamento de objetos quando houver atualização concorrente, múltiplos usuários ou telemetria persistente.

| Caminho proposto | Responsabilidade |
|---|---|
| `apps/api/app/main.py` | Inicialização, roteamento, health check e configuração Web/API |
| `apps/api/app/catalog.py` | Ler versão ativa e resolver `field_id`, município e disponibilidade |
| `apps/api/app/quality.py` | Analisar pacote e negar publicação sem integridade, série de referência ou ativos |
| `apps/api/app/routes/` | Disponibilidade, observações e, depois, recomendações e telemetria |
| `apps/api/tests/` | Contrato, autorização futura, curadoria e falhas de borda |

## Construção e critérios

- [ ] **Esqueleto:** criar projeto e comandos de teste/local; incluir endpoint de saúde sem expor segredos. Rodar testes de esquema contra exemplos do [guia 01](2026-11-14-gaiapus-01-produto-contrato.md).
- [ ] **Leitor e gate:** elaborar `load_release(manifest_path)` para resolver ativos locais, validar versão, fonte, geometria, imagem e cobertura de chuva. Arquivo faltante reprova aquele recorte; outros recortes autorizados continuam disponíveis. Trocar versão ativa sem servir mistura de arquivos. Testar pacote truncado, ID duplicado e caminho inválido.
- [ ] **Consultas:** implementar disponibilidade e observações com filtros de data inclusivos. Use resposta 200 vazia para área dentro do escopo sem pacote, 404 para recorte não publicado e 422 para datas ou hierarquia inválidas. Testar que uma seleção pelo código não retorna outro local com nome igual.
- [ ] **Web:** entregar ativos com URL de mesma origem em implantação ou configurar origens específicas em desenvolvimento. Acrescentar ETag/cache por versão e logs com ID do recorte e da requisição. Distinguir indisponibilidade temporária do serviço (503) de área sem dados.
- [ ] **Recomendações:** carregar tabela versionada e filtrar por todas as chaves obrigatórias. Testar safra antiga e ausência de classe de solo, sem inferir a classe pela coordenada.
- [ ] **Telemetria:** integrar o simulador, validar identidade, tratar reenvio idempotente e salvar dados em armazenamento persistente quando a trilha IoT entrar. Aplicar autorização por dispositivo e recorte.

Aquisição de satélite roda fora do fluxo HTTP do agricultor. UI e API só precisam do manifesto e dos derivados aprovados. Coordenadas de áreas privadas e IDs de usuários não entram nos exemplos públicos.
