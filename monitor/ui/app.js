let ws;
const timelineEl = document.getElementById('timeline');
const inspectorEl = document.getElementById('inspector-content');
const statusPulse = document.getElementById('pulse');
const statusText = document.getElementById('status-text');

let currentTurn = null; 
let allTurns = [];

function connectWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleTelemetry(data);
    };
    
    ws.onclose = () => {
        setTimeout(connectWS, 2000); // Reconnect
    };
}

function updateStatus(state, label) {
    statusPulse.className = `pulse ${state}`;
    statusText.textContent = label;
}

function handleTelemetry(event) {
    // Determine overall status
    if (event.type === 'span_start') {
        if (event.payload.name === 'turn') {
            currentTurn = { id: event.id, nodes: [], start_time: event.timestamp };
            allTurns.unshift(currentTurn);
            renderTimeline();
            updateStatus('active', 'Processing Turn...');
        } else if (currentTurn) {
            currentTurn.nodes.push({
                id: event.id,
                name: event.payload.name,
                metadata: event.payload.metadata,
                status: 'running',
                start_time: event.timestamp
            });
            renderTimeline();
            if (event.payload.name.includes('db_')) {
                updateStatus('db', 'Executing DB Query...');
            } else if (event.payload.name === 'llm_inference') {
                updateStatus('active', 'LLM Inferencing...');
            }
        }
    } else if (event.type === 'span_end') {
        if (event.payload.name === 'turn') {
            updateStatus('idle', 'Agent Idle');
            if (currentTurn) {
                currentTurn.duration = event.payload.duration_ms;
            }
            renderTimeline();
        } else if (currentTurn) {
            const node = currentTurn.nodes.find(n => n.name === event.payload.name && n.status === 'running');
            if (node) {
                node.status = event.payload.error ? 'error' : 'completed';
                node.duration = event.payload.duration_ms;
                node.end_metadata = event.payload.metadata;
                node.error = event.payload.error;
            }
            renderTimeline();
        }
    }
}

function renderTimeline() {
    if (allTurns.length === 0) return;
    timelineEl.innerHTML = '';
    
    allTurns.forEach(turn => {
        const card = document.createElement('div');
        card.className = 'trace-card';
        
        const header = document.createElement('div');
        header.className = 'trace-header';
        header.innerHTML = `
            <span>Conversation Turn</span>
            <span>${turn.duration ? turn.duration.toFixed(0) + ' ms' : 'In Progress...'}</span>
        `;
        
        const nodesContainer = document.createElement('div');
        nodesContainer.className = 'trace-nodes';
        
        turn.nodes.forEach(node => {
            const nodeEl = document.createElement('div');
            nodeEl.className = 'node';
            nodeEl.innerHTML = `
                <span class="node-title">${node.name}</span>
                <span class="node-time">${node.duration ? node.duration.toFixed(0) + ' ms' : '...'}</span>
            `;
            nodeEl.onclick = () => showInspector(node, nodeEl);
            nodesContainer.appendChild(nodeEl);
        });
        
        card.appendChild(header);
        card.appendChild(nodesContainer);
        timelineEl.appendChild(card);
    });
}

function showInspector(node, nodeEl) {
    // Clear active states
    document.querySelectorAll('.node').forEach(el => el.classList.remove('active'));
    nodeEl.classList.add('active');
    
    // Switch to inspector tab
    switchTab('inspector');
    
    let html = `<h3>Node: ${node.name}</h3>`;
    html += `<p style="color:var(--text-muted); margin-bottom: 1rem;">Duration: ${node.duration ? node.duration.toFixed(2) : 'Running'} ms</p>`;
    
    if (node.error) {
        html += `<div style="color:var(--status-error); margin-bottom:1rem;"><strong>Error:</strong> ${node.error}</div>`;
    }
    
    if (node.metadata && Object.keys(node.metadata).length > 0) {
        html += `<div class="payload-block">
            <h4>Start Payload (Input)</h4>
            <pre>${JSON.stringify(node.metadata, null, 2)}</pre>
        </div>`;
    }
    
    if (node.end_metadata && Object.keys(node.end_metadata).length > 0) {
        html += `<div class="payload-block">
            <h4>End Payload (Output)</h4>
            <pre>${JSON.stringify(node.end_metadata, null, 2)}</pre>
        </div>`;
    }
    
    inspectorEl.innerHTML = html;
}

function switchTab(tabId) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    
    document.querySelector(`.tab[onclick="switchTab('${tabId}')"]`).classList.add('active');
    document.getElementById(`tab-${tabId}`).classList.add('active');
}

// Prompt Management
async function loadPrompt() {
    try {
        const res = await fetch('/api/prompt');
        const data = await res.json();
        if (data.content) {
            document.getElementById('prompt-editor').value = data.content;
        }
    } catch (e) {
        console.error('Failed to load prompt', e);
    }
}

document.getElementById('save-prompt-btn').onclick = async () => {
    const content = document.getElementById('prompt-editor').value;
    try {
        await fetch('/api/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        alert('System prompt updated successfully!');
    } catch (e) {
        alert('Failed to update prompt.');
    }
};

// Initialize
connectWS();
loadPrompt();
