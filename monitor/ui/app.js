let ws;
const timelineEl = document.getElementById('timeline');
const statusPulse = document.getElementById('pulse');
const statusText = document.getElementById('status-text');
const inspectorEl = document.getElementById('inspector-content');

let allTurns = [];
let currentTurn = null;

function connectWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    ws.onmessage = (event) => handleTelemetry(JSON.parse(event.data));
    ws.onclose = () => setTimeout(connectWS, 2000);
}

function updateStatus(state, label) {
    statusPulse.className = `pulse ${state}`;
    statusText.textContent = label;
}

function setFlowNode(nodeId, state) {
    document.querySelectorAll('.flow-node').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.flow-line').forEach(el => el.classList.remove('active'));
    
    if (!nodeId) return;
    
    const node = document.getElementById(`node-${nodeId}`);
    if (node) node.classList.add('active');
    
    // Quick mapping for lines
    if (nodeId === 'llm') document.getElementById('line-1').classList.add('active');
    if (nodeId === 'tool') document.getElementById('line-2').classList.add('active');
    if (nodeId === 'tts') document.getElementById('line-3').classList.add('active');
}

function handleTelemetry(event) {
    if (event.type === 'span_start') {
        if (event.payload.name === 'turn') {
            currentTurn = { 
                id: event.id, 
                customer_input: event.payload.metadata.customer_input,
                llm: null,
                tool: null
            };
            allTurns.unshift(currentTurn);
            updateStatus('active', 'Listening...');
            setFlowNode('audio', 'active');
            renderTimeline();
        } else if (currentTurn) {
            if (event.payload.name === 'llm_inference') {
                updateStatus('llm', 'Brain Thinking...');
                setFlowNode('llm', 'active');
                if (event.payload.metadata.payload && event.payload.metadata.payload.messages) {
                    currentTurn.llm_messages = event.payload.metadata.payload.messages;
                } else {
                    currentTurn.llm_messages = [{"role": "system", "content": "DEBUG DATA: " + JSON.stringify(event.payload.metadata)}];
                }
                renderTimeline();
            } else if (event.payload.name.startsWith('db_tool_')) {
                updateStatus('db', 'Fetching DB...');
                setFlowNode('tool', 'active');
                currentTurn.tool = { name: event.payload.name.replace('db_tool_', ''), args: event.payload.metadata.arguments };
                renderTimeline();
            }
        }
    } else if (event.type === 'span_end') {
        if (event.payload.name === 'turn') {
            updateStatus('idle', 'Agent Idle');
            setFlowNode(null, null);
            if (currentTurn) {
                currentTurn.duration = event.payload.duration_ms;
                if (event.payload.metadata.state) {
                    currentTurn.state = event.payload.metadata.state;
                    renderState(event.payload.metadata.state);
                }
            }
        } else if (currentTurn) {
            if (event.payload.name === 'llm_inference') {
                try {
                    let text = event.payload.metadata.response;
                    if (text.includes('```json')) {
                        text = text.split('```json')[1].split('```')[0];
                    } else if (text.includes('```')) {
                        text = text.split('```')[1].split('```')[0];
                    }
                    currentTurn.llm = JSON.parse(text);
                } catch (e) {
                    currentTurn.llm = { response: "(Parsing...)" };
                }
                currentTurn.llm_duration = event.payload.duration_ms;
                renderTimeline();
            } else if (event.payload.name.startsWith('db_tool_')) {
                if (currentTurn.tool) {
                    currentTurn.tool.result = event.payload.metadata.result;
                    currentTurn.tool.error = event.payload.metadata.error;
                    currentTurn.tool.duration = event.payload.duration_ms;
                }
                renderTimeline();
            }
        }
    }
}

function buildTable(dataObj) {
    if (!dataObj) return '<em>None</em>';
    if (Array.isArray(dataObj)) {
        if (dataObj.length === 0) return '<em>Empty Array</em>';
        const keys = Object.keys(dataObj[0]);
        let html = '<table class="data-table"><thead><tr>';
        keys.forEach(k => html += `<th>${k}</th>`);
        html += '</tr></thead><tbody>';
        dataObj.forEach(row => {
            html += '<tr>';
            keys.forEach(k => html += `<td>${row[k]}</td>`);
            html += '</tr>';
        });
        html += '</tbody></table>';
        return html;
    }
    
    // Object
    const keys = Object.keys(dataObj);
    if (keys.length === 0) return '<em>Empty</em>';
    let html = '<table class="data-table"><tbody>';
    keys.forEach(k => {
        html += `<tr><th>${k}</th><td>${typeof dataObj[k] === 'object' ? JSON.stringify(dataObj[k]) : dataObj[k]}</td></tr>`;
    });
    html += '</tbody></table>';
    return html;
}

function renderTimeline() {
    if (allTurns.length === 0) return;
    timelineEl.innerHTML = '';
    
    allTurns.forEach(turn => {
        const card = document.createElement('div');
        card.className = 'turn-card';
        card.onclick = () => showInspector(turn, card);
        
        let html = `<div class="chat-bubble user-bubble">👤 ${turn.customer_input || '...'}</div>`;
        
        if (turn.llm && turn.llm.response) {
            html += `<div class="chat-bubble agent-bubble">🤖 ${turn.llm.response}</div>`;
        } else {
            html += `<div class="chat-bubble agent-bubble typing">🤖 ...</div>`;
        }
        
        if (turn.tool && turn.tool.name) {
             html += `<div class="tool-badge-small">🔧 ${turn.tool.name}</div>`;
        }
        
        card.innerHTML = html;
        timelineEl.appendChild(card);
    });
}

function showInspector(turn, cardEl) {
    document.querySelectorAll('.turn-card').forEach(el => el.classList.remove('active'));
    if (cardEl) cardEl.classList.add('active');
    
    switchTab('inspector');
    
    let html = `<h3>Turn Details <span style="font-size: 0.8rem; color: #94a3b8; font-weight: normal;">(Turn total: ${(turn.duration/1000).toFixed(2)}s)</span></h3>`;
    
    // LLM Input (Context Array)
    if (turn.llm_messages && turn.llm_messages.length > 0) {
        let msgHtml = '';
        turn.llm_messages.forEach(m => {
            let innerContent = '';
            if (m.role === 'system') {
                innerContent = parseSystemPrompt(m.content);
            } else {
                innerContent = `<div style="white-space: pre-wrap; padding: 1rem; color: #334155;">${m.content}</div>`;
            }
            
            msgHtml += `<details class="nested-accordion">
                <summary class="accordion-header">${m.role.toUpperCase()} MESSAGE</summary>
                <div class="accordion-content" style="padding: 0;">
                    ${innerContent}
                </div>
            </details>`;
        });
        html += `<details class="custom-accordion">
            <summary class="accordion-header">Fed to LLM (Context Array)</summary>
            <div class="accordion-content">${msgHtml}</div>
        </details>`;
    } else {
        html += `<details class="custom-accordion">
            <summary class="accordion-header">Fed to LLM (Context)</summary>
            <div class="accordion-content"><div style="padding: 1rem;">${turn.customer_input || 'N/A'}</div></div>
        </details>`;
    }
    
    // LLM Output
    if (turn.llm) {
        html += `<details class="custom-accordion" open>
            <summary class="accordion-header">LLM Decision <span style="font-size: 0.8rem; font-weight: normal; margin-left: 10px;">(${(turn.llm_duration/1000).toFixed(2)}s)</span></summary>
            <div class="accordion-content">
                <div style="padding: 1rem;">
                    <div style="margin-bottom: 0.5rem;"><strong>Response:</strong> ${turn.llm.response}</div>
                    <div style="margin-bottom: 0.5rem;"><strong>Order Step:</strong> <span class="badge">${turn.llm.order_step || 'idle'}</span></div>
                    ${turn.llm.tool_call ? `<div><strong>Tool Call:</strong> ${turn.llm.tool_call.name}</div>` : ''}
                </div>
            </div>
        </details>`;
    }
    
    // Tool Execution
    if (turn.tool) {
        html += `<details class="custom-accordion" open>
            <summary class="accordion-header">DB Tool: ${turn.tool.name} <span style="font-size: 0.8rem; font-weight: normal; margin-left: 10px;">(${(turn.tool.duration/1000).toFixed(2)}s)</span></summary>
            <div class="accordion-content">
                <div><strong>Arguments Sent:</strong><br>${buildTable(turn.tool.args)}</div>`;
        if (turn.tool.result) {
            html += `<div style="margin-top:10px"><strong>Data Returned:</strong><br>${buildTable(turn.tool.result)}</div>`;
        } else if (turn.tool.error) {
            html += `<div style="margin-top:10px; color:red"><strong>Error:</strong> ${turn.tool.error}</div>`;
        }
        html += `</div></details>`;
    }

    // State After Turn
    if (turn.state) {
        html += `<details class="custom-accordion" open>
            <summary class="accordion-header">State After Turn</summary>
            <div class="accordion-content">
                <div style="padding: 1rem; display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem;">
                    ${Object.keys(turn.state).filter(k => k !== 'items' && !k.startsWith('_')).map(k => `<div><strong>${k}:</strong> ${turn.state[k]}</div>`).join('')}
                </div>
            </div>
        </details>`;
    }
    
    inspectorEl.innerHTML = html;
}

function parseSystemPrompt(content) {
    let sections = content.split('## ');
    let html = '';
    
    let intro = sections[0].trim();
    if (intro) {
        let introParts = intro.split('\n\n');
        let jsonRule = introParts[0]; 
        let persona = introParts.slice(1).join('\n\n');
        
        html += `<details class="nested-accordion" open>
            <summary class="accordion-header">Persona & Output Rules</summary>
            <div class="accordion-content">
                <div class="parsed-section">
                    <div class="parsed-section-title">Agent Persona</div>
                    <div>${persona}</div>
                </div>
                <div class="parsed-section">
                    <div class="parsed-section-title">JSON Output Rules</div>
                    <div class="json-rule-box">${jsonRule.replace('CRITICAL: You MUST respond with exactly one JSON object. Never output plain text. Always use this JSON structure:\\n', '')}</div>
                </div>
            </div>
        </details>`;
    }
    
    for (let i = 1; i < sections.length; i++) {
        let lines = sections[i].split('\n');
        let title = lines[0].trim();
        let body = lines.slice(1).join('\n').trim();
        
        let bodyHtml = '';
        if (body.includes('\\n- ') || body.includes('\\n  - ') || body.includes('\\n1. ')) {
            let listItems = body.split('\\n').filter(l => l.trim().length > 0);
            bodyHtml = '<ul class="parsed-list">';
            listItems.forEach(li => {
                bodyHtml += `<li>${li.trim()}</li>`;
            });
            bodyHtml += '</ul>';
        } else {
            bodyHtml = `<div style="white-space: pre-wrap;">${body}</div>`;
        }
        
        html += `<details class="nested-accordion">
            <summary class="accordion-header">${title}</summary>
            <div class="accordion-content">${bodyHtml}</div>
        </details>`;
    }
    
    return html;
}

function renderState(stateObj) {
    const viewer = document.getElementById('state-viewer');
    viewer.innerHTML = '';
    Object.keys(stateObj).forEach(key => {
        if (key === 'items' || key.startsWith('_')) return; // hide messy internals
        viewer.innerHTML += `
            <div class="state-item">
                <span class="state-key">${key}</span>
                <span class="state-val">${stateObj[key]}</span>
            </div>
        `;
    });
}

function switchTab(tabId) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`.tab[onclick="switchTab('${tabId}')"]`).classList.add('active');
    document.getElementById(`tab-${tabId}`).classList.add('active');
}

// Prompt Dissection Logic
async function loadPrompt() {
    try {
        const res = await fetch('/api/prompt');
        const data = await res.json();
        if (data.content) {
            // Strip out python variable declaration if it exists
            let cleanStr = data.content;
            if (cleanStr.includes('ORDER_SYSTEM_PROMPT = """')) {
                cleanStr = cleanStr.split('ORDER_SYSTEM_PROMPT = """')[1].split('"""')[0].trim();
            }
            
            document.getElementById('prompt-editor').value = data.content; // hidden full text
            
            // Dissect by Markdown Headers (## )
            const parts = cleanStr.split(/##\s+/);
            
            document.getElementById('prompt-persona').value = parts[0] ? parts[0].trim() : '';
            document.getElementById('prompt-rules').value = parts.length > 1 ? '## ' + parts[1].trim() : '';
            document.getElementById('prompt-format').value = parts.length > 2 ? '## ' + parts[2].trim() : '';
        }
    } catch (e) {
        console.error('Failed to load prompt', e);
    }
}

document.getElementById('save-prompt-btn').onclick = async () => {
    const p1 = document.getElementById('prompt-persona').value;
    const p2 = document.getElementById('prompt-rules').value;
    const p3 = document.getElementById('prompt-format').value;
    
    let innerContent = p1;
    if (p2) innerContent += '\n\n' + p2;
    if (p3) innerContent += '\n\n' + p3;
    
    // Re-wrap in python string
    const fullPy = `ORDER_SYSTEM_PROMPT = """\n${innerContent}\n"""\n`;
    
    try {
        await fetch('/api/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: fullPy })
        });
        alert('Prompt updated beautifully!');
    } catch (e) {
        alert('Failed to update prompt.');
    }
};

async function loadInitialState() {
    try {
        const res = await fetch('/api/state');
        const stateData = await res.json();
        renderState(stateData);
    } catch(e) {
        console.error("Failed to load initial state", e);
    }
}

connectWS();
loadPrompt();
loadInitialState();
