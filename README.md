# 📹 Verificador de Posição de Câmeras de PDV & DVRs

Sistema inteligente de auditoria e monitoramento contínuo do posicionamento visual de câmeras de segurança instaladas sobre frentes de caixa (PDVs), integrando **Visão Computacional (OpenCV)**, **conexão direta com DVRs/NVRs (RTSP e HTTP Snapshot)** e interface web em tempo real.

---

## 📌 Sumário
- [Visão Geral](#-visão-geral)
- [Funcionalidades Principais](#-funcionalidades-principais)
- [O que foi Desenvolvido e Corrigido](#-o-que-foi-desenvolvido-e-corrigido)
- [Arquitetura e Estrutura de Pastas](#-arquitetura-e-estrutura-de-pastas)
- [Motor de Visão Computacional (Vision Engine)](#-motor-de-visão-computacional-vision-engine)
- [Endpoints da API REST](#-endpoints-da-api-rest)
- [Como Executar o Projeto](#-como-executar-o-projeto)
- [Guia Rápido de Uso](#-guia-rápido-de-uso)

---

## 🎯 Visão Geral

Nas operações de varejo e frente de caixa, as câmeras de CFTV voltadas para os PDVs são essenciais para prevenção de perdas e auditoria de transações. No entanto, vibrações mecânicas, manutenções, limpeza ou impactos físicos frequentemente alteram o ângulo da câmera sem que a equipe perceba.

O **Verificador de Posição** resolve esse problema comparando periodicamente o frame atual da câmera contra uma **imagem de referência calibrada** do caixa, calculando com precisão matemática:
1. **Ângulo de Rotação / Inclinação (°)**
2. **Deslocamento / Translação (pixels)**
3. **Nitidez e Foco da Lente (Variância do Laplaciano)**
4. **Obstrução / Cegueira Visual (lente coberta ou caixa apagado)**

---

## ✨ Funcionalidades Principais

- **Auditoria Individual e em Lote:** Varredura de todos os caixas da loja em menos de 2 segundos com processamento multithread.
- **Suporte Multi-Fabricante de DVRs/NVRs:**
  - Intelbras (MHDX / Multi-HD)
  - Hikvision
  - Dahua
  - Axis Communications
  - Uniview
  - RTSP Genérico e Câmeras IP Avulsas
- **Puxada de Imagens ao Vivo do DVR:**
  - Extração instantânea via **HTTP Snapshot CGI** (`/cgi-bin/snapshot.cgi?channel=N`).
  - Fallback automático para fluxo de vídeo contínuo **RTSP** com decodificação OpenCV.
- **Auto-Auditoria em Segundo Plano (Background Scan):** Varredura automática configurável (30s, 60s, 300s ou 600s) com atualização em tempo real do painel.
- **Laboratório de Diagnóstico Interativo:**
  - Visualização lado a lado (Imagem Atual vs. Imagem de Referência Calibrada).
  - Telemetria matemática com grau de rotação (`+0.0°`), deslocamento em pixels e nível de confiança (`%`).
  - Simulação de cenários com um clique (`Alinhada 0.0°`, `Desvio Leve +3.8°`, `Torta -7.8°`, `Crítica +12.5°`).
  - Upload de fotos e vídeos (`.avi` / `.mp4`) do computador para teste e calibração imediata.
- **Relatório Formal de Auditoria:** Rota `/report` pronta para impressão e exportação formal com assinaturas técnicas, tabela de conformidade e fotos comparativas.

---

## 🛠️ O que foi Desenvolvido e Corrigido

Durante a evolução do projeto, as seguintes implementações e correções estruturais foram realizadas:

### 1. Conexão com DVRs de Loja e Extração ao Vivo
- Criado o módulo `core/dvr_api.py` com conectores para Intelbras, Hikvision, Dahua e RTSP.
- Implementado suporte para teste de portas de rede (RTSP `554` e HTTP `80`) via sockets TCP ultrarrápidos para diagnóstico imediato de conectividade na VPN da loja.
- Adicionada geração em lote de canais ao cadastrar um DVR (cria automaticamente registros vinculados "PDV 01", "PDV 02", etc.).

### 2. Correção de Concorrência e Gravação Atômica (Thread-Safety)
- **Problema resolvido:** Ao acionar "Verificar Todas" com 6 threads concorrentes, gravações simultâneas no arquivo `cameras.json` causavam corrida de escrita, truncando o arquivo para 0 bytes e apagando as câmeras.
- **Solução implementada:** 
  - Adicionado `threading.RLock()` no `CameraManager` e `DVRManager`.
  - Implementada gravação atômica com arquivo temporário (`.tmp`) e substituição segura (`os.replace`).
  - Criação de backup automático (`cameras.json.bak`) a cada salvamento para blindar a integridade dos dados.

### 3. Correção dos Botões de Cadastro e Interface do Usuário
- **Problema resolvido:** Conflitos entre manipuladores de evento `onclick` e `onsubmit` nos botões de cadastro impediam a abertura fluida dos modais ou duplicavam submissões.
- **Solução implementada:**
  - Fluxo de validação de formulários nativo HTML5 integrado com JavaScript.
  - Indicadores de carregamento nos botões com recuperação em caso de falha.
  - Sistema de avisos flutuantes (*Toasts*) com feedback instantâneo de sucesso ou erro.

### 4. Importação e Calibração Automática dos 27 Caixas da Loja
- Criado importador em `DVRManager.import_user_media()` que vincula automaticamente os 27 caixas reais (`PDV 101` a `118` e `PDV 8001` a `8009`) aos vídeos e fotos disponibilizados nas pastas `frames/` e `videos/`.
- Todos os 27 PDVs foram calibrados com referências individuais salvas em `data/references/` e status 100% alinhado.

---

## 📁 Arquitetura e Estrutura de Pastas

```text
Verificador de posição/
├── frames/                      # Fotos reais de teste dos caixas da loja
├── videos/                      # Vídeos reais gravados pelo DVR da loja
├── verificador_posicao/         # Aplicação principal
│   ├── app.py                   # Servidor Web Flask e Rotas REST
│   ├── core/                    # Módulos centrais de lógica e visão
│   │   ├── camera_manager.py    # Gerenciador de câmeras, persistência JSON segura e calibração
│   │   ├── dvr_manager.py       # Gerenciador de DVRs e criação de canais em lote
│   │   ├── dvr_api.py           # Conector de rede (Snapshots HTTP CGI e RTSP)
│   │   ├── vision_engine.py     # Motor OpenCV (ORB, Homografia, Rotação, Blur e Obstrução)
│   │   └── rtsp_builder.py      # Formatador de URLs RTSP por fabricante
│   ├── data/                    # Banco de dados local em formato JSON e mídia gerada
│   │   ├── cameras.json         # Base cadastral das câmeras e última telemetria
│   │   ├── dvrs.json            # Base de DVRs cadastrados
│   │   ├── references/          # Fotos padrão de referência calibradas de cada PDV
│   │   └── snapshots/           # Últimos frames analisados com sobreposição visual
│   ├── static/                  # Arquivos estáticos da interface web
│   │   ├── css/
│   │   │   └── style.css        # Folha de estilos moderna com tema escuro e glassmorphism
│   │   └── js/
│   │       └── app.js           # Controlador do frontend (Fetch API, modais, filtros e toasts)
│   └── templates/
│       ├── index.html           # Dashboard operacional principal
│       └── report.html          # Relatório formal de auditoria técnica para impressão
```

---

## 🧠 Motor de Visão Computacional (Vision Engine)

O alinhamento visual é calculado através de um pipeline em [vision_engine.py](file:///h:/Meus%20Projetos/Verificador%20de%20posi%C3%A7%C3%A3o/verificador_posicao/core/vision_engine.py):

1. **Pré-processamento:** Redimensionamento normalizado e conversão para escala de cinza com equalização de contraste (*CLAHE*).
2. **Obstrução e Iluminação:** Análise do desvio padrão e brilho médio. Se o frame for excessivamente escuro ou uniforme, é classificado como `OBSTRUCTED`.
3. **Análise de Foco e Nitidez:** Cálculo da variância do operador Laplaciano ($\sigma^2_{\Delta}$). Valores baixos alertam para lente desfocada ou empoeirada.
4. **Extração de Pontos Chave (ORB Detector):** Identificação de até 1.500 pontos de interesse invariantemente distribuídos.
5. **Casamento de Características (BFMatcher + KNN):** Aplicação do teste de razão de Lowe para descartar falsos positivos.
6. **Matriz de Homografia e Transformação Afim:**
   - Cálculo via `cv2.findHomography` com RANSAC ou `cv2.estimateAffinePartial2D`.
   - Decomposição matemática:
     $$\theta = \arctan2(R_{0,1}, R_{0,0}) \times \frac{180}{\pi}$$
7. **Classificação de Status:**
   - 🟢 **ALINHADA (`ALIGNED`):** $|\theta| < 3.0^\circ$ e Deslocamento $< 45\text{ px}$.
   - 🟡 **ATENÇÃO (`WARNING`):** $3.0^\circ \le |\theta| \le 5.0^\circ$ ou $45\text{ px} \le \text{Deslocamento} \le 80\text{ px}$.
   - 🔴 **TORTA / DESALINHADA (`CROOKED`):** $|\theta| > 5.0^\circ$ ou Deslocamento $> 80\text{ px}$.

---

## 🌐 Endpoints da API REST

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/` | Interface principal do Dashboard |
| `GET` | `/report` | Visualização do Relatório Técnico de Auditoria |
| `GET` | `/api/cameras` | Lista todas as câmeras cadastradas e resumo de métricas |
| `POST` | `/api/cameras` | Cadastra um novo PDV individual |
| `PUT` | `/api/cameras/<id>` | Atualiza configurações de um PDV |
| `DELETE` | `/api/cameras/<id>` | Remove um PDV do monitoramento |
| `POST` | `/api/cameras/<id>/analyze` | Executa análise de alinhamento em um PDV específico |
| `POST` | `/api/cameras/analyze_all` | Executa análise em lote em todos os PDVs cadastrados |
| `POST` | `/api/cameras/<id>/calibrate` | Define o frame atual como o padrão de referência calibrado |
| `GET` | `/api/cameras/<id>/snapshot` | Retorna a imagem do último frame anotado com telemetria |
| `GET` | `/api/cameras/<id>/reference` | Retorna a imagem de referência padrão calibrada |
| `POST` | `/api/cameras/<id>/scenario` | Aplica um cenário de teste simulado (alinhada, torta, crítica) |
| `POST` | `/api/cameras/<id>/upload_image`| Envia uma foto local do computador para teste ou calibração |
| `POST` | `/api/cameras/<id>/upload_video`| Envia um vídeo local do computador para processamento |
| `GET` | `/api/dvrs` | Lista todos os DVRs cadastrados e status |
| `POST` | `/api/dvrs` | Cadastra um DVR e pode gerar canais automaticamente |
| `POST` | `/api/dvrs/<id>/sync` | Puxa frames ao vivo de todos os canais de um DVR específico |
| `POST` | `/api/dvrs/test_connection` | Testa conectividade TCP nas portas RTSP (554) e HTTP (80) |
| `POST` | `/api/import_user_media` | Carrega os 27 PDVs oficiais com fotos e vídeos reais de teste |
| `POST` | `/api/auto_scan/toggle` | Liga/Desliga a varredura contínua em segundo plano |

---

## 🚀 Como Executar o Projeto

### Pré-requisitos
- **Python 3.10** ou superior instalado.
- Pacotes necessários instalados:
  ```bash
  pip install flask opencv-python numpy
  ```

### Iniciando a Aplicação
No diretório `verificador_posicao`, execute:
```bash
python app.py --port 5050
```

Abra o seu navegador web em:
- **Acesso Local:** `http://localhost:5050`
- **Acesso na Rede Local:** `http://<SEU_IP_LOCAL>:5050`

---

## 📖 Guia Rápido de Uso

1. **Dashboard:** Ao abrir a página, você verá os cards com todos os 27 caixas monitorados, taxa de conformidade, total de câmeras alinhadas e eventuais desvios.
2. **⚡ Verificar Todas:** Clique neste botão no cabeçalho para realizar uma auditoria completa instantânea em todos os caixas.
3. **➕ Adicionar PDV:** Permite cadastrar uma câmera individual, vinculá-la a um DVR existente e testar a porta de conexão em tempo real.
4. **📼 Cadastrar DVR:** Cadastre um gravador da loja (ex: Intelbras MHDX em `10.59.151.131`), escolha a quantidade de canais e deixe marcada a opção para gerar todos os caixas automaticamente.
5. **🔬 Diagnóstico Detalhado:** Em qualquer card de PDV, clique em *Diagnóstico* para:
   - Ver a foto de referência e a foto atual sobrepostas lado a lado.
   - Conferir o parecer técnico e grau de rotação.
   - Puxar frame ao vivo direto do canal do DVR.
   - Testar o comportamento com cenários pré-configurados ou submeter fotos/vídeos gravados.
   - Definir uma nova imagem de calibração padrão caso o caixa tenha mudado de posição legitimamente.
6. **📑 Relatório:** Clique no botão *Relatório* no canto superior direito para abrir o relatório formatado de auditoria, ideal para exportação em PDF ou impressão para a gerência de loja.
