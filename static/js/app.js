/**
 * VERIFICADOR DE POSIÇÃO — Frontend Application Controller v5
 * Gerenciamento dinâmico de Câmeras de PDV e Equipamentos DVR / NVR.
 * Conexão RTSP ao vivo, telemetria matemática, calibração e testes com material real da loja.
 */

let allCameras = [];
let allDvrs = [];
let currentFilter = 'ALL';
let currentDvrFilter = 'ALL';
let currentClienteFilter = 'ALL';
let currentLojaFilter = 'ALL';
let fleetTree = {};   // {cliente: [lojas]} do Mongo (toda a frota, sem as lojas "[...]")
let currentSearch = '';
let activeDiagnosticCameraId = null;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}

async function initApp() {
    setupEventListeners();
    await loadFleetTree();   // todos os clientes/lojas do Mongo pros filtros
    await loadDvrs();
    await loadCameras();
}

// ==============================================================================
// CONFIGURAÇÃO DE EVENT LISTENERS
// ==============================================================================
function setupEventListeners() {
    // Filtros por Status
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            currentFilter = e.currentTarget.dataset.filter;
            renderCameras();
        });
    });

    // Filtro por DVR
    const dvrSelect = document.getElementById('dvrFilterSelect');
    if (dvrSelect) {
        dvrSelect.addEventListener('change', (e) => {
            currentDvrFilter = e.target.value;
            renderCameras();
        });
    }

    // Filtro por Cliente (loja depende do cliente escolhido)
    const clienteSelect = document.getElementById('clienteFilterSelect');
    if (clienteSelect) {
        clienteSelect.addEventListener('change', (e) => {
            currentClienteFilter = e.target.value;
            currentLojaFilter = 'ALL';
            populateClienteLojaSelects();
            renderCameras();
        });
    }
    const lojaSelect = document.getElementById('lojaFilterSelect');
    if (lojaSelect) {
        lojaSelect.addEventListener('change', (e) => {
            currentLojaFilter = e.target.value;
            togglePuxarLoja();
            renderCameras();
        });
    }

    // Fecha o menu do usuário ao clicar fora
    document.addEventListener('click', (e) => {
        const um = document.getElementById('userMenu');
        if (um && um.classList.contains('open') && !um.contains(e.target)) um.classList.remove('open');
    });

    // Busca Textual
    const searchInput = document.getElementById('cameraSearchInput');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            currentSearch = e.target.value.toLowerCase().trim();
            renderCameras();
        });
    }

    // Botões do cabeçalho usam onclick no index.html — não registrar listener aqui (dispararia 2x)

    // Upload de CSV
    const csvFileInput = document.getElementById('csvFileInput');
    if (csvFileInput) {
        csvFileInput.addEventListener('change', handleCsvUpload);
    }

    // Fechar modais ao clicar no overlay de fundo
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                closeModal(overlay.id);
            }
        });
    });

    // Fechar modais com tecla ESC
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal-overlay.active').forEach(m => closeModal(m.id));
        }
    });
}

// ==============================================================================
// SISTEMA DE TOAST NOTIFICATIONS
// ==============================================================================
function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';

    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ==============================================================================
// CARREGAMENTO DE DADOS (DVRS E CÂMERAS)
// ==============================================================================
async function loadDvrs() {
    try {
        const resp = await fetch('/api/dvrs');
        const data = await resp.json();
        allDvrs = data.dvrs || [];
        populateDvrSelects();
    } catch (err) {
        console.error('Erro ao carregar DVRs:', err);
    }
}

function populateDvrSelects() {
    try {
        // 1. Dropdown de filtro no topo
        const filterSelect = document.getElementById('dvrFilterSelect');
        if (filterSelect) {
            const currentVal = filterSelect.value;
            let html = '<option value="ALL">Todos os DVRs</option>';
            (allDvrs || []).forEach(d => {
                html += `<option value="${d.id}">${d.name || d.id} (${d.ip || ''})</option>`;
            });
            filterSelect.innerHTML = html;
            if (currentVal && (allDvrs || []).some(d => d.id === currentVal)) {
                filterSelect.value = currentVal;
            }
        }

        // 2. Dropdown de vinculação no formulário de Adicionar PDV
        const camDvrSelect = document.getElementById('camDvrSelect');
        if (camDvrSelect) {
            let html = '<option value="">Nenhum (Câmera IP / RTSP Avulso)</option>';
            (allDvrs || []).forEach(d => {
                const bStr = (d.brand || 'intelbras').toUpperCase();
                html += `<option value="${d.id}">${d.name || d.id} • ${bStr} (${d.ip || ''})</option>`;
            });
            camDvrSelect.innerHTML = html;
        }
    } catch (err) {
        console.error('Erro em populateDvrSelects:', err);
    }
}

async function loadFleetTree() {
    try {
        const resp = await fetch('/api/mongo/fleet_tree');
        const data = await resp.json();
        if (data.success) fleetTree = data.tree || {};
    } catch (err) { /* sem Mongo: dropdowns caem só pras câmeras */ }
}

function populateClienteLojaSelects() {
    try {
        const clienteSel = document.getElementById('clienteFilterSelect');
        const lojaSel = document.getElementById('lojaFilterSelect');
        if (!clienteSel || !lojaSel) return;
        // Clientes: toda a frota do Mongo + os que já têm câmera
        const clientes = [...new Set([
            ...Object.keys(fleetTree),
            ...(allCameras || []).map(c => c.cliente).filter(Boolean),
        ])].sort();
        clienteSel.innerHTML = '<option value="ALL">Todos os clientes</option>' +
            clientes.map(c => `<option value="${c}">${c}</option>`).join('');
        clienteSel.value = clientes.includes(currentClienteFilter) ? currentClienteFilter : 'ALL';
        // Lojas do cliente escolhido (do Mongo) + as que já têm câmera
        const doMongo = currentClienteFilter === 'ALL'
            ? Object.values(fleetTree).flat()
            : (fleetTree[currentClienteFilter] || []);
        const dasCameras = (allCameras || [])
            .filter(c => currentClienteFilter === 'ALL' || c.cliente === currentClienteFilter)
            .map(c => c.loja).filter(Boolean);
        const lojas = [...new Set([...doMongo, ...dasCameras])].sort();
        lojaSel.innerHTML = '<option value="ALL">Todas as lojas</option>' +
            lojas.map(l => `<option value="${l}">${l}</option>`).join('');
        lojaSel.value = lojas.includes(currentLojaFilter) ? currentLojaFilter : 'ALL';
        togglePuxarLoja();
    } catch (err) {
        console.error('Erro em populateClienteLojaSelects:', err);
    }
}

async function loadCameras() {
    try {
        const resp = await fetch('/api/cameras');
        const data = await resp.json();
        allCameras = data.cameras || [];
        updateStats(data.stats);
        populateClienteLojaSelects();
        renderCameras();
    } catch (err) {
        console.error('Erro ao carregar câmeras:', err);
        showToast('Erro ao carregar dados do servidor', 'error');
    }
}

function updateStats(stats) {
    if (!stats) return;
    document.getElementById('statTotalVal').textContent = stats.total || 0;
    document.getElementById('statAlignedVal').textContent = stats.aligned || 0;
    document.getElementById('statWarningVal').textContent = stats.warning || 0;
    document.getElementById('statCrookedVal').textContent = stats.crooked || 0;
}

// ==============================================================================
// RENDERIZAÇÃO DO GRID DE CÂMERAS E EMPTY STATE
// ==============================================================================
function renderCameras() {
    const grid = document.getElementById('camerasGrid');
    if (!grid) return;

    const filtered = allCameras.filter(cam => {
        // Filtro por Cliente / Loja (MongoDB)
        if (currentClienteFilter !== 'ALL' && cam.cliente !== currentClienteFilter) return false;
        if (currentLojaFilter !== 'ALL' && cam.loja !== currentLojaFilter) return false;

        // Filtro por DVR
        if (currentDvrFilter !== 'ALL') {
            if (cam.dvr_id !== currentDvrFilter) return false;
        }

        // Filtro de Status
        const status = cam.last_check_status || 'PENDING';
        const filterStatus = status === 'NO_AISLE' ? 'CROOKED' : status;  // sem corredor também exige reposicionar
        if (currentFilter !== 'ALL' && filterStatus !== currentFilter) {
            return false;
        }

        // Filtro de Texto
        if (currentSearch) {
            const name = (cam.name || '').toLowerCase();
            const ip = (cam.ip || '').toLowerCase();
            const dvrName = (cam.dvr_name || '').toLowerCase();
            const brand = (cam.brand || '').toLowerCase();
            const posCode = String(cam.pos_code || '');
            const channel = String(cam.channel || '');

            const cliente = (cam.cliente || '').toLowerCase();
            const loja = (cam.loja || '').toLowerCase();
            return name.includes(currentSearch) ||
                   ip.includes(currentSearch) ||
                   dvrName.includes(currentSearch) ||
                   brand.includes(currentSearch) ||
                   posCode.includes(currentSearch) ||
                   channel.includes(currentSearch) ||
                   cliente.includes(currentSearch) ||
                   loja.includes(currentSearch);
        }
        return true;
    });

    if (filtered.length === 0) {
        if (allCameras.length === 0) {
            // Estado inicial vazio com cartões de ação rápida
            grid.innerHTML = `
                <div class="empty-state-box">
                    <div style="font-size: 3.5rem;">📹</div>
                    <div>
                        <h2 class="empty-state-title">Nenhum PDV monitorado ainda</h2>
                        <p class="empty-state-desc">
                            Cadastre seu equipamento DVR para importar todos os caixas da loja de uma vez, 
                            adicione um caixa avulso ou carregue os arquivos de fotos e vídeos de teste.
                        </p>
                    </div>

                    <div class="empty-action-cards">
                        <div class="empty-action-card" onclick="openAddDvrModal()">
                            <div class="empty-action-icon">📼</div>
                            <h4>Cadastrar DVR Completo</h4>
                            <p>Informe o IP do DVR (ex: Intelbras 10.59.151.131) e crie todos os 16 canais de PDV automaticamente.</p>
                            <span class="btn btn-primary btn-sm" style="margin-top: 0.5rem;">Cadastrar DVR</span>
                        </div>

                        <div class="empty-action-card" onclick="openAddCameraModal()">
                            <div class="empty-action-icon">➕</div>
                            <h4>Adicionar PDV Individual</h4>
                            <p>Cadastre um caixa específico vinculando a uma câmera IP ou canal individual de DVR.</p>
                            <span class="btn btn-secondary btn-sm" style="margin-top: 0.5rem;">Adicionar PDV</span>
                        </div>

                        <div class="empty-action-card" onclick="importUserMedia()">
                            <div class="empty-action-icon">📂</div>
                            <h4>Carregar Material da Loja</h4>
                            <p>Importa instantaneamente as 27 fotos e vídeos reais de PDVs fornecidos nas pastas do sistema.</p>
                            <span class="btn btn-accent btn-sm" style="margin-top: 0.5rem;">Carregar Testes</span>
                        </div>
                    </div>
                </div>
            `;
        } else {
            // Nenhum resultado correspondente aos filtros
            grid.innerHTML = `
                <div style="grid-column: 1 / -1; text-align: center; padding: 4rem 1rem; color: var(--text-muted);">
                    <div style="font-size: 3rem; margin-bottom: 1rem;">🔍</div>
                    <h3>Nenhuma câmera encontrada para os filtros selecionados</h3>
                    <p style="margin-top: 0.5rem;">Tente selecionar "Todos os DVRs" ou limpar o campo de busca.</p>
                </div>
            `;
        }
        return;
    }

    grid.innerHTML = filtered.map(cam => createCameraCard(cam)).join('');
}

function createCameraCard(cam) {
    const status = cam.last_check_status || 'PENDING';
    const angle = parseFloat(cam.last_rotation_deg || 0);
    const shift = parseFloat(cam.last_translation_px || 0);

    let badgeClass = 'badge-offline';
    let badgeLabel = 'Pendente';
    let valClass = '';

    if (status === 'ALIGNED') {
        badgeClass = 'badge-aligned';
        badgeLabel = 'Alinhada';
        valClass = 'val-aligned';
    } else if (status === 'MILD_DRIFT') {
        badgeClass = 'badge-warning';
        badgeLabel = 'Desvio Leve';
        valClass = 'val-warning';
    } else if (status === 'CROOKED') {
        badgeClass = 'badge-crooked';
        badgeLabel = 'Torta / Crítica';
        valClass = 'val-crooked';
    } else if (status === 'OFFLINE') {
        badgeClass = 'badge-offline';
        badgeLabel = 'Sem Sinal';
    } else if (status === 'PENDING') {
        badgeClass = 'badge-offline';
        badgeLabel = cam.last_status_text || 'Conferir';  // "Medindo Nível" ou "Corredor: IA em Dúvida"
    } else if (status === 'NO_AISLE') {
        badgeClass = 'badge-crooked';
        badgeLabel = 'Sem Corredor';
    }

    const angleDisplay = angle > 0 ? `+${angle.toFixed(1)}°` : `${angle.toFixed(1)}°`;
    const snapshotUrl = `/api/cameras/${cam.id}/snapshot?t=${Date.now()}`;
    const dvrTag = cam.dvr_name ? `📼 ${cam.dvr_name} • Canal ${cam.channel || 1}` : `${cam.brand.toUpperCase()} • ${cam.ip || 'Simulação'}`;

    return `
        <div class="camera-card" id="card-${cam.id}">
            <div class="card-top">
                <img src="${snapshotUrl}" class="card-thumbnail" alt="${cam.name}" loading="lazy">
                <div class="card-status-badge ${badgeClass}">
                    <span class="pulse-dot"></span>
                    ${badgeLabel}
                </div>
            </div>

            <div class="card-content">
                <div class="camera-header-info">
                    <div>
                        <h4 class="camera-title">${cam.name}</h4>
                        <div class="camera-meta" title="${cam.ip || 'Local'}">${dvrTag}</div>
                    </div>
                </div>

                <div class="telemetry-row">
                    <div class="telemetry-item">
                        <span class="telemetry-label">Inclinação</span>
                        <span class="telemetry-val ${valClass}">${angleDisplay}</span>
                    </div>
                    <div class="telemetry-item">
                        <span class="telemetry-label">Desloc.</span>
                        <span class="telemetry-val">${shift.toFixed(1)}px</span>
                    </div>
                    <div class="telemetry-item">
                        ${cam.last_aisle_pct != null
                            ? `<span class="telemetry-label">Corredor</span><span class="telemetry-val ${status === 'NO_AISLE' ? 'val-crooked' : ''}">${Math.round(cam.last_aisle_pct)}%</span>`
                            : `<span class="telemetry-label">Confiança</span><span class="telemetry-val">${cam.last_confidence || 0}%</span>`}
                    </div>
                </div>

                <div class="card-actions">
                    <button class="btn btn-primary btn-main" onclick="analyzeSingleCamera('${cam.id}', event)" title="Verificar alinhamento agora">
                        ⚡ Verificar
                    </button>
                    <button class="btn btn-secondary btn-main" onclick="openDiagnosticModal('${cam.id}')" title="Abrir diagnóstico detalhado e telemetria">
                        🔬 Diagnóstico
                    </button>
                    <button class="btn btn-secondary btn-icon" onclick="calibrateCamera('${cam.id}')" title="Calibrar: Definir frame atual como padrão de referência">
                        🎯
                    </button>
                    <button class="btn btn-danger btn-icon" onclick="deleteCamera('${cam.id}')" title="Remover PDV do monitoramento">
                        🗑️
                    </button>
                </div>
            </div>
        </div>
    `;
}

// ==============================================================================
// MODAIS (ABERTURA, FECHAMENTO E VALIDAÇÃO)
// ==============================================================================
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) {
        console.error(`Modal não encontrado: ${modalId}`);
        return;
    }
    modal.style.display = 'flex';
    modal.style.opacity = '1';
    modal.style.pointerEvents = 'auto';
    modal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    if (modalId === 'diagnosticModal') stopLiveView();
    modal.classList.remove('active');
    modal.style.opacity = '0';
    modal.style.pointerEvents = 'none';
    modal.style.display = 'none';
}

// Modal Adicionar PDV
function openAddCameraModal() {
    try {
        const form = document.getElementById('addCameraForm');
        if (form) form.reset();
        populateDvrSelects();

        const testResEl = document.getElementById('camTestResult');
        if (testResEl) {
            testResEl.textContent = 'Pronto para testar';
            testResEl.style.color = 'var(--text-dim)';
        }
    } catch (err) {
        console.error('Erro ao abrir modal de PDV:', err);
    }
    openModal('addCameraModal');
}

// Modal Cadastrar DVR
function openAddDvrModal() {
    try {
        const form = document.getElementById('addDvrForm');
        if (form) form.reset();

        const dvrTestRes = document.getElementById('dvrTestResult');
        if (dvrTestRes) {
            dvrTestRes.textContent = 'Clique para testar portas RTSP (554) e HTTP (80)';
            dvrTestRes.style.color = 'var(--text-dim)';
        }

        // Pré-preenche com IP comum da loja para agilizar
        const ipInput = document.getElementById('dvrIp');
        if (ipInput && !ipInput.value) {
            ipInput.value = '10.59.151.131';
        }
    } catch (err) {
        console.error('Erro ao abrir modal de DVR:', err);
    }
    openModal('addDvrModal');
}

// Quando o usuário seleciona um DVR no formulário de PDV
function handleDvrSelectChange(dvrId) {
    if (!dvrId) return;
    const dvr = (allDvrs || []).find(d => d.id === dvrId);
    if (!dvr) return;

    // Preenche automaticamente IP, fabricante, porta e credenciais
    const brandEl = document.getElementById('camBrand');
    const ipEl = document.getElementById('camIp');
    const portEl = document.getElementById('camPort');
    const userEl = document.getElementById('camUser');
    const passEl = document.getElementById('camPass');

    if (brandEl) brandEl.value = dvr.brand || 'intelbras';
    if (ipEl) ipEl.value = dvr.ip || '';
    if (portEl) portEl.value = dvr.port || 554;
    if (userEl) userEl.value = dvr.username || 'admin';
    if (passEl) passEl.value = dvr.password || '';
}

// ==============================================================================
// SALVAR CÂMERA / PDV
// ==============================================================================
async function handleSaveCamera(e) {
    if (e && e.preventDefault) e.preventDefault();
    const btnSubmit = document.getElementById('btnSubmitCamera');
    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.textContent = '⏳ Salvando...';
    }

    const nameInput = document.getElementById('camName');
    const nameVal = nameInput ? nameInput.value.trim() : '';
    if (!nameVal) {
        showToast('Por favor, informe o nome / identificação do PDV.', 'error');
        if (nameInput) nameInput.focus();
        if (btnSubmit) { btnSubmit.disabled = false; btnSubmit.textContent = '💾 Salvar Câmera'; }
        return;
    }

    const selectedDvrId = document.getElementById('camDvrSelect')?.value || '';
    const selectedDvr = (allDvrs || []).find(d => d.id === selectedDvrId);

    const data = {
        name: nameVal,
        dvr_id: selectedDvrId || '',
        dvr_name: selectedDvr ? selectedDvr.name : '',
        brand: document.getElementById('camBrand')?.value || 'intelbras',
        ip: document.getElementById('camIp')?.value.trim() || '',
        port: parseInt(document.getElementById('camPort')?.value) || 554,
        username: document.getElementById('camUser')?.value.trim() || 'admin',
        password: document.getElementById('camPass')?.value || '',
        channel: parseInt(document.getElementById('camChannel')?.value) || 1,
        substream: document.getElementById('camSubstream') ? document.getElementById('camSubstream').checked : true,
        is_simulation: document.getElementById('camSimulation') ? document.getElementById('camSimulation').checked : false,
        sim_angle: parseFloat(document.getElementById('camSimAngle')?.value) || 0.0
    };

    try {
        const resp = await fetch('/api/cameras', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const res = await resp.json();

        if (res.success) {
            closeModal('addCameraModal');
            showToast(`PDV "${data.name}" cadastrado com sucesso!`, 'success');
            await loadCameras();
            await loadDvrs();
        } else {
            showToast(`Falha ao salvar: ${res.message || 'Erro desconhecido'}`, 'error');
        }
    } catch (err) {
        console.error('Erro na requisição ao salvar câmera:', err);
        showToast('Erro de comunicação ao salvar câmera.', 'error');
    } finally {
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.textContent = '💾 Salvar Câmera';
        }
    }
}

// ==============================================================================
// SALVAR DVR E GERAR PDVS EM LOTE
// ==============================================================================
async function handleSaveDvr(e) {
    if (e && e.preventDefault) e.preventDefault();
    const btnSubmit = document.getElementById('btnSubmitDvr');
    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.textContent = '⏳ Cadastrando DVR e Gerando Canais...';
    }

    const nameInput = document.getElementById('dvrName');
    const ipInput = document.getElementById('dvrIp');
    const nameVal = nameInput ? nameInput.value.trim() : '';
    const ipVal = ipInput ? ipInput.value.trim() : '';

    if (!nameVal) {
        showToast('Por favor, informe a identificação do DVR.', 'error');
        if (nameInput) nameInput.focus();
        if (btnSubmit) { btnSubmit.disabled = false; btnSubmit.textContent = '💾 Salvar DVR e Criar PDVs'; }
        return;
    }

    if (!ipVal) {
        showToast('Por favor, informe o endereço IP do DVR.', 'error');
        if (ipInput) ipInput.focus();
        if (btnSubmit) { btnSubmit.disabled = false; btnSubmit.textContent = '💾 Salvar DVR e Criar PDVs'; }
        return;
    }

    const data = {
        name: nameVal,
        brand: document.getElementById('dvrBrand')?.value || 'intelbras',
        ip: ipVal,
        port: parseInt(document.getElementById('dvrPort')?.value) || 554,
        http_port: parseInt(document.getElementById('dvrHttpPort')?.value) || 80,
        username: document.getElementById('dvrUser')?.value.trim() || 'admin',
        password: document.getElementById('dvrPass')?.value || '',
        channel_count: parseInt(document.getElementById('dvrChannels')?.value) || 16,
        auto_create_pdvs: document.getElementById('dvrAutoCreatePdvs') ? document.getElementById('dvrAutoCreatePdvs').checked : true
    };

    try {
        const resp = await fetch('/api/dvrs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const res = await resp.json();

        if (res.success) {
            closeModal('addDvrModal');
            const msg = res.detected_active_channels
                ? `DVR "${data.name}" cadastrado! Detectados ${res.detected_active_channels} canais ativos no DVR → ${res.created_pdvs_count || 0} PDVs criados.`
                : `DVR "${data.name}" cadastrado! ${res.created_pdvs_count || 0} canais gerados (o DVR não respondeu à detecção; usei a quantidade informada).`;
            showToast(msg, 'success', 8000);
            await loadDvrs();
            await loadCameras();
        } else {
            showToast(`Falha ao salvar DVR: ${res.message || 'Erro desconhecido'}`, 'error');
        }
    } catch (err) {
        console.error('Erro ao salvar DVR:', err);
        showToast('Erro de comunicação ao salvar DVR.', 'error');
    } finally {
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.textContent = '💾 Salvar DVR e Criar PDVs';
        }
    }
}

// ==============================================================================
// TESTES DE CONEXÃO EM TEMPO REAL (SOCKET TCP / RTSP)
// ==============================================================================
async function testCameraConnection() {
    const ip = document.getElementById('camIp').value.trim();
    const port = parseInt(document.getElementById('camPort').value) || 554;
    const testEl = document.getElementById('camTestResult');

    if (!ip) {
        if (testEl) {
            testEl.textContent = '⚠️ Informe o IP antes de testar';
            testEl.style.color = 'var(--status-warning)';
        }
        return;
    }

    if (testEl) {
        testEl.textContent = '⏳ Testando portas RTSP...';
        testEl.style.color = 'var(--accent-cyan)';
    }

    try {
        const resp = await fetch('/api/dvrs/test_connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, port, http_port: 80 })
        });
        const res = await resp.json();

        if (testEl) {
            if (res.success) {
                testEl.textContent = `✅ Respondendo (${res.latency_ms}ms)`;
                testEl.style.color = 'var(--status-aligned)';
            } else {
                testEl.textContent = `❌ Sem resposta (${ip}:${port})`;
                testEl.style.color = 'var(--status-crooked)';
            }
        }
    } catch (err) {
        if (testEl) {
            testEl.textContent = '❌ Erro ao testar conexão';
            testEl.style.color = 'var(--status-crooked)';
        }
    }
}

async function testDvrConnection() {
    const ip = document.getElementById('dvrIp').value.trim();
    const port = parseInt(document.getElementById('dvrPort').value) || 554;
    const httpPort = parseInt(document.getElementById('dvrHttpPort').value) || 80;
    const testEl = document.getElementById('dvrTestResult');

    if (!ip) {
        if (testEl) {
            testEl.textContent = '⚠️ Informe o IP do DVR antes de testar';
            testEl.style.color = 'var(--status-warning)';
        }
        return;
    }

    if (testEl) {
        testEl.textContent = `⏳ Testando conexão com ${ip}...`;
        testEl.style.color = 'var(--accent-cyan)';
    }

    try {
        const resp = await fetch('/api/dvrs/test_connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, port, http_port: httpPort })
        });
        const res = await resp.json();

        if (testEl) {
            if (res.success) {
                testEl.textContent = `✅ ${res.message}`;
                testEl.style.color = 'var(--status-aligned)';
            } else {
                testEl.textContent = `❌ ${res.message}`;
                testEl.style.color = 'var(--status-crooked)';
            }
        }
    } catch (err) {
        if (testEl) {
            testEl.textContent = '❌ Erro ao testar DVR';
            testEl.style.color = 'var(--status-crooked)';
        }
    }
}

// ==============================================================================
// IMPORTAÇÃO DE MATERIAL DE TESTE (27 PDVS COM FOTOS/VÍDEOS REAIS)
// ==============================================================================
async function importUserMedia() {
    const btn = document.getElementById('btnImportMedia');
    const origText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Importando fotos/vídeos...';
    }

    try {
        const resp = await fetch('/api/import_user_media', { method: 'POST' });
        const res = await resp.json();
        if (res.success) {
            showToast(res.message, 'success');
            await loadDvrs();
            await loadCameras();
        } else {
            showToast(`Falha na importação: ${res.message}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao importar material de teste.', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origText;
        }
    }
}

// ==============================================================================
// ANÁLISE DE ALINHAMENTO (INDIVIDUAL E EM LOTE)
// ==============================================================================
// ==============================================================================
// PUXAR IMAGENS AO VIVO DO DVR E AUDITORIA AUTOMÁTICA
// ==============================================================================
async function syncDvrLive() {
    const btn = document.getElementById('btnSyncDvrLive');
    const origText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Conectando ao DVR e puxando frames...';
    }

    try {
        let resp, res;
        // Se um DVR específico estiver filtrado, sincroniza ele diretamente
        if (currentDvrFilter && currentDvrFilter !== 'ALL') {
            resp = await fetch(`/api/dvrs/${currentDvrFilter}/sync`, { method: 'POST' });
            res = await resp.json();
            if (res.success) {
                showToast(`DVR "${res.dvr_name}": ${res.aligned} caixas alinhados, ${res.crooked} tortos!`, 'success');
            } else {
                showToast(`Falha ao sincronizar DVR: ${res.message}`, 'error');
            }
        } else {
            // Sincroniza todos os canais de DVR ao vivo
            resp = await fetch('/api/cameras/analyze_all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pull_from_dvr: true })
            });
            res = await resp.json();
            if (res.success) {
                showToast(`Frames do DVR puxados e analisados com sucesso em ${res.results.length} PDVs!`, 'success');
            } else {
                showToast(`Falha ao puxar frames do DVR.`, 'error');
            }
        }

        await loadDvrs();
        await loadCameras();
    } catch (err) {
        console.error('Erro ao sincronizar DVR:', err);
        showToast('Erro de comunicação ao puxar imagens do DVR.', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origText;
        }
    }
}

// Auto-Auditoria Contínua em Background
let autoScanTimer = null;

async function toggleAutoScan() {
    const btn = document.getElementById('btnAutoScanToggle');
    const intervalSelect = document.getElementById('autoScanIntervalSelect');
    const interval = parseInt(intervalSelect?.value) || 60;

    const isCurrentlyOn = btn?.classList.contains('active-scan');
    const newTargetState = !isCurrentlyOn;

    try {
        const resp = await fetch('/api/auto_scan/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: newTargetState, interval_seconds: interval })
        });
        const res = await resp.json();

        if (res.success) {
            updateAutoScanUI(res.state.enabled, res.state.interval_seconds);
            if (res.state.enabled) {
                showToast(`Auto-Auditoria ativada! Varrendo DVR a cada ${interval}s.`, 'success');
                startAutoScanPolling();
            } else {
                showToast('Auto-Auditoria desativada.', 'info');
                stopAutoScanPolling();
            }
        }
    } catch (err) {
        showToast('Erro ao alternar auto-auditoria.', 'error');
    }
}

function updateAutoScanUI(enabled, interval) {
    const btn = document.getElementById('btnAutoScanToggle');
    if (!btn) return;

    if (enabled) {
        btn.classList.add('active-scan');
        btn.classList.remove('btn-secondary');
        btn.classList.add('btn-accent');
        btn.innerHTML = '🟢 ATIVA';
    } else {
        btn.classList.remove('active-scan');
        btn.classList.remove('btn-accent');
        btn.classList.add('btn-secondary');
        btn.innerHTML = 'OFF';
    }
}

function startAutoScanPolling() {
    stopAutoScanPolling();
    // Atualiza a tela a cada 15 segundos para exibir frames e telemetrias frescas
    autoScanTimer = setInterval(async () => {
        await loadCameras();
    }, 15000);
}

function stopAutoScanPolling() {
    if (autoScanTimer) {
        clearInterval(autoScanTimer);
        autoScanTimer = null;
    }
}

async function changeAutoScanInterval(seconds) {
    try {
        await fetch('/api/auto_scan/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval_seconds: parseInt(seconds) || 60 })
        });
        showToast(`Intervalo de auto-auditoria alterado para ${seconds}s.`, 'info');
    } catch (err) {
        console.error(err);
    }
}

// Análise Individual com puxada ao vivo do DVR
async function analyzeSingleCamera(camId, event) {
    if (event) event.stopPropagation();
    const btn = event?.currentTarget;
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Puxando DVR...';
    }

    try {
        const resp = await fetch(`/api/cameras/${camId}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pull_from_dvr: true })
        });
        const res = await resp.json();
        if (res.success) {
            await loadCameras();
            const angleStr = res.rotation_deg !== undefined ? `${res.rotation_deg > 0 ? '+' : ''}${res.rotation_deg.toFixed(1)}°` : '0°';
            showToast(`Auditoria PDV: ${res.status_text || 'OK'} (${angleStr}) — ${res.method_used || ''}`, 'info');
        } else {
            showToast(`Falha na análise: ${res.message || 'Sem conexão com o DVR'}`, 'error');
            await loadCameras();
        }
    } catch (err) {
        console.error('Erro na análise:', err);
        showToast('Erro ao processar análise da câmera.', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '⚡ Verificar';
        }
    }
}

async function analyzeAllCameras() {
    const btn = document.getElementById('btnAnalyzeAll');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Auditando caixas...';
    }

    try {
        const resp = await fetch('/api/cameras/analyze_all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pull_from_dvr: true })  // puxa frame fresco do DVR; sem isso só rescore do cache (não mudava nada)
        });
        const res = await resp.json();
        await loadCameras();
        showToast(`Auditoria concluída em ${allCameras.length} caixas!`, 'success');
    } catch (err) {
        showToast('Erro ao executar varredura em lote.', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '⚡ Verificar Todas';
        }
    }
}

function togglePuxarLoja() {
    const btn = document.getElementById('btnPuxarLoja');
    if (!btn) return;
    const specific = currentLojaFilter && currentLojaFilter !== 'ALL';
    const jaImportada = specific && (allCameras || []).some(c => c.loja === currentLojaFilter);
    btn.style.display = (specific && !jaImportada) ? '' : 'none';
}

async function puxarLoja() {
    if (!currentLojaFilter || currentLojaFilter === 'ALL') {
        showToast('Selecione uma loja em "🏬 Loja" primeiro.', 'info', 5000);
        return;
    }
    const loja = currentLojaFilter;
    showToast(`Puxando "${loja}" do Mongo (abrindo túnel SSH e detectando câmeras)...`, 'info', 10000);
    try {
        const resp = await fetch('/api/mongo/import_fleet', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ loja })
        });
        const res = await resp.json();
        if (res.success) {
            await loadDvrs();
            await loadCameras();
            if (res.total_pdvs > 0) {
                showToast(`Loja "${loja}": ${res.total_pdvs} câmera(s) puxada(s). Use "Ver ao Vivo" nos caixas.`, 'success', 8000);
            } else if (res.dvrs && res.dvrs.every(d => !d.dvr_novo)) {
                showToast(`Loja "${loja}" já estava cadastrada.`, 'info', 6000);
            } else {
                showToast(`Loja "${loja}": o DVR não respondeu (IP da VPN em transição ou offline). Tente de novo.`, 'error', 8000);
            }
        } else {
            showToast(`Falha ao puxar a loja: ${res.message || 'erro'}`, 'error', 8000);
        }
    } catch (err) {
        showToast('Erro ao puxar a loja.', 'error');
    }
}

async function redetectChannels() {
    if (!currentDvrFilter || currentDvrFilter === 'ALL') {
        showToast('Selecione um DVR no filtro "📼 DVR" antes de re-detectar.', 'info', 6000);
        return;
    }
    const dvr = (allDvrs || []).find(d => d.id === currentDvrFilter);
    if (!confirm(`Re-detectar canais de "${dvr ? dvr.name : currentDvrFilter}"?\n\nIsso relê os canais ativos do DVR (nomes reais) e REMOVE os canais que não existem mais nele.`)) return;
    showToast('Lendo os canais ativos do DVR...', 'info', 6000);
    try {
        const resp = await fetch(`/api/dvrs/${currentDvrFilter}/redetect`, { method: 'POST' });
        const res = await resp.json();
        if (res.success) {
            showToast(`Detectados ${res.detectados} canais ativos — ${res.criados} criados, ${res.renomeados} renomeados, ${res.removidos} removidos.`, 'success', 8000);
            await loadCameras();
        } else {
            showToast(`Falha: ${res.message || 'não foi possível re-detectar'}`, 'error', 8000);
        }
    } catch (err) {
        showToast('Erro ao re-detectar canais.', 'error');
    }
}

async function importJanus(input) {
    const file = input.files && input.files[0];
    input.value = '';  // permite reimportar o mesmo arquivo
    if (!file) return;
    showToast(`Lendo ${file.name} e consultando os DVRs...`, 'info', 6000);
    try {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/dvrs/import_janus', { method: 'POST', body: fd });
        const res = await resp.json();
        if (res.success) {
            const nDvr = (res.dvrs || []).length;
            showToast(`Importados ${nDvr} DVR(s) e ${res.total_pdvs} PDV(s) do Janus.`, 'success', 8000);
            await loadCameras();
        } else {
            showToast(`Falha ao importar: ${res.message || 'arquivo inválido'}`, 'error', 8000);
        }
    } catch (err) {
        showToast('Erro ao importar o arquivo do Janus.', 'error');
    }
}

// ==============================================================================
// CALIBRAÇÃO E EXCLUSÃO
// ==============================================================================
async function calibrateCamera(camId) {
    if (!confirm('Deseja definir a imagem atual deste caixa como a Referência Oficial de Alinhamento?')) return;

    try {
        const resp = await fetch(`/api/cameras/${camId}/calibrate`, { method: 'POST' });
        const res = await resp.json();
        if (res.success) {
            showToast(res.message, res.message.includes('ATENÇÃO') ? 'error' : 'success', 8000);
            await loadCameras();
        } else {
            showToast(`Erro: ${res.message}`, 'error');
        }
    } catch (err) {
        showToast('Erro na calibração.', 'error');
    }
}

async function deleteCamera(camId) {
    if (!confirm('Tem certeza que deseja remover esta câmera do monitoramento?')) return;
    try {
        await fetch(`/api/cameras/${camId}`, { method: 'DELETE' });
        showToast('Câmera removida com sucesso.', 'info');
        await loadCameras();
        await loadDvrs();
    } catch (err) {
        showToast('Erro ao excluir câmera.', 'error');
    }
}

// ==============================================================================
// MODAL DE DIAGNÓSTICO DETALHADO
// ==============================================================================
function openDiagnosticModal(camId) {
    const cam = allCameras.find(c => c.id === camId);
    if (!cam) return;

    const titleEl = document.getElementById('diagModalTitle');
    const metaEl = document.getElementById('diagModalMeta');
    const refImg = document.getElementById('diagRefImg');
    const currImg = document.getElementById('diagCurrImg');
    const angleEl = document.getElementById('diagAngleVal');
    const shiftEl = document.getElementById('diagShiftVal');
    const confEl = document.getElementById('diagConfVal');
    const methodEl = document.getElementById('diagMethodVal');

    titleEl.textContent = `Diagnóstico: ${cam.name}`;
    const dvrInfo = cam.dvr_name ? ` • DVR: ${cam.dvr_name} (CH ${cam.channel})` : '';
    metaEl.textContent = `${cam.brand.toUpperCase()}${dvrInfo} • IP: ${cam.ip || 'Simulação'} • RTSP: ${cam.masked_url || ''}`;

    refImg.src = `/api/cameras/${cam.id}/reference?t=${Date.now()}`;
    stopLiveView();
    currImg.src =`/api/cameras/${cam.id}/snapshot?t=${Date.now()}`;

    const angle = parseFloat(cam.last_rotation_deg || 0);
    angleEl.textContent = `${angle > 0 ? '+' : ''}${angle.toFixed(2)}°`;
    shiftEl.textContent = `${(cam.last_translation_px || 0).toFixed(1)} px`;
    confEl.textContent = `${cam.last_confidence || 0}%`;
    methodEl.textContent = cam.last_check_method || 'Comparação com Referência';

    // Atualiza Parecer do Alinhamento no Modal
    const statusBanner = document.getElementById('diagStatusBanner');
    const statusBadge = document.getElementById('diagStatusBadge');
    const reasonText = document.getElementById('diagReasonText');

    const status = cam.last_check_status || 'OFFLINE';
    if (statusBadge && reasonText && statusBanner) {
        if (status === 'ALIGNED') {
            statusBadge.className = 'status-badge status-aligned';
            statusBadge.textContent = '🟢 ALINHADA (100% OK)';
            reasonText.textContent = `Alinhamento verificado com sucesso. Rotação (${angle.toFixed(1)}°) e deslocamento físico (${(cam.last_translation_px || 0).toFixed(1)}px) aprovados.`;
            statusBanner.style.background = 'rgba(16, 185, 129, 0.12)';
            statusBanner.style.borderColor = 'var(--status-aligned)';
        } else if (status === 'MILD_DRIFT') {
            statusBadge.className = 'status-badge status-warning';
            statusBadge.textContent = '🟡 DESVIO LEVE (ATENÇÃO)';
            reasonText.textContent = `Atenção operacional: Desvio angular de ${angle.toFixed(1)}° detectado. Recomenda-se calibrar ou ajustar fixação da câmera.`;
            statusBanner.style.background = 'rgba(245, 158, 11, 0.12)';
            statusBanner.style.borderColor = 'var(--status-warning)';
        } else if (status === 'CROOKED') {
            statusBadge.className = 'status-badge status-crooked';
            statusBadge.textContent = '🔴 TORTA / CRÍTICA';
            reasonText.textContent = `Alerta Crítico: Câmera com rotação de ${angle.toFixed(1)}° fora da tolerância! O enquadramento do caixa de atendimento está comprometido.`;
            statusBanner.style.background = 'rgba(239, 68, 68, 0.14)';
            statusBanner.style.borderColor = 'var(--status-crooked)';
        } else if (status === 'NO_AISLE') {
            statusBadge.className = 'status-badge status-crooked';
            statusBadge.textContent = '🔴 CORREDOR INSUFICIENTE';
            const lado = cam.last_aisle_side === 'cima' ? 'acima' : cam.last_aisle_side === 'baixo' ? 'abaixo' : null;
            reasonText.textContent = `A câmera mostra só ${Math.round(cam.last_aisle_pct || 0)}% de corredor de carrinhos. A IA de carrinhos precisa ver o corredor à frente do caixa${lado ? ` (fica ${lado} do PDV)` : ''}: reposicione a câmera para enquadrá-lo.`;
            statusBanner.style.background = 'rgba(239, 68, 68, 0.14)';
            statusBanner.style.borderColor = 'var(--status-crooked)';
        } else if (status === 'PENDING') {
            statusBadge.className = 'status-badge status-offline';
            statusBadge.textContent = `⏳ ${(cam.last_status_text || 'Conferir').toUpperCase()}`;
            reasonText.textContent = cam.last_reason || 'O app ainda não tem certeza sobre este caixa e tenta de novo a cada verificação.';
            statusBanner.style.background = 'rgba(100, 116, 139, 0.15)';
            statusBanner.style.borderColor = 'var(--text-dim)';
        } else if (status === 'OBSTRUCTED') {
            statusBadge.className = 'status-badge status-offline';
            statusBadge.textContent = '⚫ OBSTRUÍDA / ESCURA';
            reasonText.textContent = 'Falha visual: Imagem sem iluminação no caixa ou lente coberta.';
            statusBanner.style.background = 'rgba(100, 116, 139, 0.15)';
            statusBanner.style.borderColor = 'var(--text-dim)';
        } else {
            statusBadge.className = 'status-badge status-offline';
            statusBadge.textContent = '⚪ OFFLINE / SEM RESPOSTA';
            reasonText.textContent = 'Câmera não respondeu ao stream ou DVR offline.';
            statusBanner.style.background = 'rgba(100, 116, 139, 0.15)';
            statusBanner.style.borderColor = 'var(--text-dim)';
        }
    }

    activeDiagnosticCameraId = cam.id;
    const techLink = document.getElementById('diagTechLink');
    if (techLink) techLink.href = `/ajuste/${cam.id}`;

    const modalCalibrateBtn = document.getElementById('diagCalibrateBtn');
    if (modalCalibrateBtn) {
        modalCalibrateBtn.onclick = async () => {
            await calibrateCamera(cam.id);
            closeModal('diagnosticModal');
        };
    }

    openModal('diagnosticModal');
}

async function diagPullDvrNow() {
    if (!activeDiagnosticCameraId) return;
    const btn = document.getElementById('btnDiagPullDvr');
    const orig = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Puxando frame do DVR...';
    }
    try {
        const resp = await fetch(`/api/cameras/${activeDiagnosticCameraId}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pull_from_dvr: true })
        });
        const res = await resp.json();
        await loadCameras();
        openDiagnosticModal(activeDiagnosticCameraId);
        if (res.success) {
            showToast(`Frame puxado do DVR! Status: ${res.status_text || 'OK'} (${res.rotation_deg > 0 ? '+' : ''}${res.rotation_deg}°)`, 'success');
        } else {
            showToast(`Aviso DVR: ${res.message || 'Falha ao puxar frame'}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao puxar frame do DVR.', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = orig;
        }
    }
}
window.diagPullDvrNow = diagPullDvrNow;

// Stream MJPEG ao vivo no lugar do frame analisado
function toggleLiveView() {
    const img = document.getElementById('diagCurrImg');
    const btn = document.getElementById('btnDiagLive');
    if (!img || !activeDiagnosticCameraId) return;
    if (img.dataset.live) {
        stopLiveView();
        img.src = `/api/cameras/${activeDiagnosticCameraId}/snapshot?t=${Date.now()}`;
        return;
    }
    img.dataset.live = '1';
    img.onerror = () => { showToast('Sem sinal ao vivo deste canal (DVR inacessível?).', 'error'); stopLiveView(); };
    img.src = `/api/cameras/${activeDiagnosticCameraId}/live?t=${Date.now()}`;
    if (btn) btn.innerHTML = '⏹ Parar ao Vivo';
}

function stopLiveView() {
    const img = document.getElementById('diagCurrImg');
    const btn = document.getElementById('btnDiagLive');
    if (img && img.dataset.live) {
        delete img.dataset.live;
        img.onerror = null;
        img.src = '';  // fecha a conexão do stream
    }
    if (btn) btn.innerHTML = '▶ Ver ao Vivo';
}
window.toggleLiveView = toggleLiveView;

async function applyTestScenario(scenarioName) {
    if (!activeDiagnosticCameraId) return;

    try {
        const resp = await fetch(`/api/cameras/${activeDiagnosticCameraId}/scenario`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scenario: scenarioName })
        });
        const res = await resp.json();
        if (res.success) {
            await loadCameras();
            openDiagnosticModal(activeDiagnosticCameraId);
            showToast(`Cenário "${scenarioName}" aplicado!`, 'info');
        } else {
            showToast(`Erro ao aplicar cenário: ${res.message || 'Falha'}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao carregar cenário de teste.', 'error');
    }
}

async function uploadCustomImage(asReference) {
    if (!activeDiagnosticCameraId) return;
    const input = asReference ? document.getElementById('uploadRefImageInput') : document.getElementById('uploadTestImageInput');
    const file = input?.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('as_reference', asReference ? '1' : '0');

    try {
        const resp = await fetch(`/api/cameras/${activeDiagnosticCameraId}/upload_image`, {
            method: 'POST',
            body: formData
        });
        const res = await resp.json();
        if (res.success) {
            showToast(asReference ? 'Nova imagem de referência salva!' : 'Foto de teste analisada com sucesso!', 'success');
            await loadCameras();
            openDiagnosticModal(activeDiagnosticCameraId);
        } else {
            showToast(`Falha: ${res.message}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao enviar imagem.', 'error');
    } finally {
        if (input) input.value = '';
    }
}

async function uploadCustomVideo() {
    if (!activeDiagnosticCameraId) return;
    const input = document.getElementById('uploadVideoInput');
    const file = input?.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    const btn = input.closest('label');
    const origText = btn ? btn.innerHTML : '';
    if (btn) btn.innerHTML = '⏳ Processando...';

    try {
        const resp = await fetch(`/api/cameras/${activeDiagnosticCameraId}/upload_video`, {
            method: 'POST',
            body: formData
        });
        const res = await resp.json();
        if (res.success) {
            showToast('Vídeo carregado e analisado com sucesso!', 'success');
            await loadCameras();
            openDiagnosticModal(activeDiagnosticCameraId);
        } else {
            showToast(`Falha: ${res.message || 'Erro no vídeo'}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao enviar vídeo.', 'error');
    } finally {
        if (btn) btn.innerHTML = origText;
        if (input) input.value = '';
    }
}

async function handleCsvUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/cameras/import_csv', {
            method: 'POST',
            body: formData
        });
        const res = await resp.json();
        if (res.success) {
            showToast(`${res.imported_count} câmeras importadas com sucesso!`, 'success');
            await loadCameras();
        } else {
            showToast(`Falha na importação: ${res.message}`, 'error');
        }
    } catch (err) {
        showToast('Erro ao enviar arquivo CSV.', 'error');
    } finally {
        e.target.value = '';
    }
}

// ==============================================================================
// EXPOSIÇÃO GLOBAL PARA ACESSO IMEDIATO
// ==============================================================================
window.openAddCameraModal = openAddCameraModal;
window.openAddDvrModal = openAddDvrModal;
window.closeModal = closeModal;
window.handleDvrSelectChange = handleDvrSelectChange;
window.handleSaveCamera = handleSaveCamera;
window.handleSaveDvr = handleSaveDvr;
window.testCameraConnection = testCameraConnection;
window.testDvrConnection = testDvrConnection;
window.importUserMedia = importUserMedia;
window.syncDvrLive = syncDvrLive;
window.toggleAutoScan = toggleAutoScan;
window.changeAutoScanInterval = changeAutoScanInterval;
window.analyzeSingleCamera = analyzeSingleCamera;
window.analyzeAllCameras = analyzeAllCameras;
window.calibrateCamera = calibrateCamera;
window.deleteCamera = deleteCamera;
window.openDiagnosticModal = openDiagnosticModal;
window.applyTestScenario = applyTestScenario;
window.uploadCustomImage = uploadCustomImage;
window.uploadCustomVideo = uploadCustomVideo;
window.showToast = showToast;
