(function () {

const SHOW_QUICK_PROMPTS = false;

const AI_CHAT_PROMPTS = {
    "Purchase Invoice": [
        { label: "Compare image with invoice", prompt: "Compare the uploaded image with this invoice and highlight any mismatches" },
        { label: "Summarize this invoice", prompt: "Summarize this invoice" },
        { label: "Total amount?", prompt: "What is the Total amount?" },
        { label: "List all items", prompt: "List all line items with quantities and amounts" },
        { label: "Is this overdue?", prompt: "Is this invoice overdue? Check due date vs today" },
        { label: "GST compliance check", prompt: "Check GST compliance — verify GSTIN, HSN code, tax amount, place of supply are present" },
    ],
    "Purchase Receipt": [
        { label: "Compare delivery challan", prompt: "Compare the uploaded delivery challan with this Purchase Receipt and highlight any quantity or item mismatches" },
        { label: "Any quantity mismatches?", prompt: "Are there any quantity mismatches between received and ordered?" },
        { label: "List items received", prompt: "List all items received with quantities" },
    ],
    "Payment Entry": [
        { label: "Verify payment proof", prompt: "Verify the uploaded payment proof against this Payment Entry — check amount, date, and reference number" },
        { label: "Does amount match?", prompt: "Does the amount in the uploaded image match this payment entry?" },
        { label: "Payment details", prompt: "Summarize this payment entry" },
    ],
    "Sales Order": [
        { label: "Compare customer PO", prompt: "Compare the uploaded customer Purchase Order with this Sales Order — highlight item, quantity, rate, and delivery date mismatches" },
        { label: "Any mismatches?", prompt: "Are there any mismatches between the uploaded document and this Sales Order?" },
        { label: "Delivery dates match?", prompt: "Do the delivery dates in the uploaded document match this Sales Order?" },
    ],
    "Quotation": [
        { label: "Compare competitor quote", prompt: "Compare the uploaded competitor quote with our Quotation — show where we are priced higher and by how much" },
        { label: "Where are we priced higher?", prompt: "List all items where our price is higher than the uploaded competitor quote" },
        { label: "Summarize quotation", prompt: "Summarize this quotation" },
    ],
    "Purchase Order": [
        { label: "Compare packing list", prompt: "Compare the uploaded packing list with this Purchase Order — catch any wrong items or quantities" },
        { label: "Any wrong items?", prompt: "Are there any wrong items or quantities in the uploaded packing list compared to this Purchase Order?" },
        { label: "List ordered items", prompt: "List all items ordered with quantities and rates" },
    ],
    "Expense Claim": [
        { label: "Verify receipt", prompt: "Verify the uploaded receipt against this expense claim — check amount, date, and vendor name" },
        { label: "Amount correct?", prompt: "Does the receipt amount match the claimed amount?" },
        { label: "Date within policy?", prompt: "Is the receipt date within the claim policy period?" },
    ],
};

const DEFAULT_PROMPTS = [
    { label: "Sales invoices this month", prompt: "Show me the sales invoices created this month" },
    { label: "Total customers", prompt: "What is the total number of customers?" },
    { label: "Top 5 customers who paid more", prompt: "List out the top 5 customers who paid the most" },
    { label: "Pending purchase invoices", prompt: "Show me all draft and pending purchase invoices" },
    { label: "Create purchase invoice", prompt: "I want to create a purchase invoice from an uploaded invoice image. Please guide me or process the uploaded image." },
    { label: "Create quotation", prompt: "I want to create a quotation from an uploaded quotation image. Please guide me or process the uploaded image." },
    { label: "Customer outstanding", prompt: "Show me the outstanding payment amounts from customers" },
    { label: "Overdue purchase invoices", prompt: "Show me all purchase invoices that are past due date and not yet paid" },
    { label: "Total sales this month", prompt: "What is the total sales revenue for this month?" },
    { label: "Low stock items", prompt: "Show me the items which have less stock" },
    { label: "Top suppliers", prompt: "List the top 5 suppliers by total purchase amount" },
];

const _company = frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.company;
const ai_title = _company ? ('I' + _company) : 'I&lt;company_name&gt;';

// Site's own configured logo (System Settings / Website > App Logo) instead
// of a hardcoded emoji — falls back to the robot emoji if the site hasn't
// set one.
const _site_logo_url = frappe.boot && frappe.boot.app_logo_url;
function _ai_logo_html(size) {
    if (_site_logo_url) {
        return `<img src="${_site_logo_url}" style="width:${size}px;height:${size}px;object-fit:contain;border-radius:4px;vertical-align:middle;">`;
    }
    return '🤖';
}

// frappe.markdown() shares Frappe's global showdown.Converter, which is
// constructed with no options — Showdown's "tables" extension is OFF by
// default, so even well-formed "| a | b |" markdown renders as raw text
// instead of an HTML table. Use our own converter, scoped to this widget,
// with tables explicitly enabled.
function _ai_markdown_to_html(text) {
    if (!window._ai_md_converter) {
        // frappe.markdown() lazily creates frappe.md2html on first call —
        // call it once so the showdown library is guaranteed to be loaded
        // before we construct our own tables-enabled converter from it.
        frappe.markdown("");
        window._ai_md_converter = new frappe.md2html.constructor({ tables: true });
    }
    return window._ai_md_converter.makeHtml(text);
}

// frappe.confirm()'s modal renders at Bootstrap's default z-index, which is
// lower than this widget's panel (9998) / floating button (9999) — without
// this, the confirm dialog appears visually behind the panel (worse once
// the panel's been dragged wider) and its buttons become unclickable.
// On top of the z-index fix, Bootstrap centers .modal-dialog against the
// FULL viewport width — with the side panel covering the right portion of
// the screen, that centering pushes the dialog box (and its footer/buttons)
// partly or fully behind the panel. #ai-modal-active is toggled on <body>
// only while our confirm dialog is open, so we can force that specific
// dialog into the visible left region without touching other modals.
(function _inject_ai_modal_zindex_fix() {
    if (document.getElementById('ai-modal-zfix')) return;
    const style = document.createElement('style');
    style.id = 'ai-modal-zfix';
    style.textContent = `
        .modal-backdrop { z-index: 100000 !important; }
        .modal { z-index: 100001 !important; }
        body.ai-modal-active .modal-dialog {
            position: fixed !important;
            top: 30vh !important;
            left: 5vw !important;
            margin: 0 !important;
            width: min(500px, 85vw) !important;
        }
        /* Belt-and-braces: whatever the modal's own z-index resolves to,
           explicitly drop the AI floating button BELOW it while our confirm
           dialog is open, instead of relying solely on out-stacking it.
           (The side panel's own z-index is dropped directly via JS instead
           of a body-descendant selector here, since on mobile the panel is
           attached outside <body> and this selector wouldn't match it.) */
        body.ai-modal-active #ai-floating-btn {
            z-index: 1 !important;
        }
        #ai-side-panel .ai-chat-history table {
            border-collapse: collapse;
            width: 100%;
            margin: 6px 0;
            font-size: 11.5px;
        }
        #ai-side-panel .ai-chat-history table th,
        #ai-side-panel .ai-chat-history table td {
            border: 1px solid #e2e8f0;
            padding: 4px 6px;
            text-align: left;
        }
        #ai-side-panel .ai-chat-history table th {
            background: #f1f5f9;
            font-weight: 600;
        }
    `;
    document.head.appendChild(style);
})();

setTimeout(setup_ai_widget, 2000);

function setup_ai_widget() {
    if (!frappe.boot.enable_ai_chat) return;
    if (document.getElementById('ai-floating-btn')) return;

    const btn = document.createElement('div');
    btn.id = 'ai-floating-btn';
    btn.innerHTML = _ai_logo_html(30);
    btn.title = ai_title;
    btn.style.cssText = `
        position: fixed;
        bottom: 30px;
        right: 30px;
        width: 52px;
        height: 52px;
        background: #d1f5dd;
        color: white;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 22px;
        cursor: pointer;
        z-index: 9999;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        transition: transform 0.2s;
    `;

    btn.addEventListener('mouseenter', () => btn.style.transform = 'scale(1.1)');
    btn.addEventListener('mouseleave', () => btn.style.transform = 'scale(1)');
    btn.addEventListener('click', open_ai_panel);

    document.body.appendChild(btn);
}

function open_ai_panel() {
    if (document.getElementById('ai-side-panel')) return;

    let image_data = null;
    let image_mime = null;

    const route = frappe.get_route();
    const on_form = route && route[0] === 'Form' && route[1] && route[2];
    const prompts = (on_form && AI_CHAT_PROMPTS[route[1]]) ? AI_CHAT_PROMPTS[route[1]] : DEFAULT_PROMPTS;
    const prompt_buttons = prompts.map(p =>
        `<button class="ai-suggestion-btn btn btn-xs btn-default" data-prompt="${p.prompt}" style="margin:2px 2px 4px 0;">${p.label}</button>`
    ).join('');

    // Role-based mode: header dropdown lets the user see/pick which matched
    // role their chat should use, when they have 2+ options (see
    // boot_session's ai_role_choices). Defaults to the first option so the
    // chat works immediately from the very first message — no blocking
    // in-chat prompt needed for the common case — and whatever they land on
    // (default or manually switched) persists per logged-in user via
    // localStorage, auto-selected again next time they open the chat.
    const ai_role_choices = frappe.boot.ai_role_choices || [];
    const _role_storage_key = 'ai_selected_role::' + frappe.session.user;
    let selected_ai_role = null;
    if (ai_role_choices.length > 1) {
        const stored_role = localStorage.getItem(_role_storage_key);
        if (stored_role && ai_role_choices.includes(stored_role)) {
            selected_ai_role = stored_role;
        } else {
            selected_ai_role = ai_role_choices[0];
            localStorage.setItem(_role_storage_key, selected_ai_role);
        }
    }
    const role_dropdown_html = ai_role_choices.length > 1
        ? `<select class="ai-role-dropdown" style="font-size:11px;padding:2px 6px;border-radius:4px;border:none;background:rgba(255,255,255,0.9);color:#333;">
            ${ai_role_choices.map(r => `<option value="${r}"${r === selected_ai_role ? ' selected' : ''}>${r}</option>`).join('')}
           </select>`
        : '';

    // Hide floating button
    const floatBtn = document.getElementById('ai-floating-btn');
    if (floatBtn) floatBtn.style.display = 'none';

    // Create side panel — on mobile (<=768px) it expands to fill the whole
    // screen instead of staying a narrow fixed-width strip, since a 380px
    // panel on a phone leaves most of the chat unusably cramped.
    const is_mobile_view = window.innerWidth <= 768;
    const panel = document.createElement('div');
    panel.id = 'ai-side-panel';
    panel.style.cssText = is_mobile_view ? `
        position: fixed;
        left: 0;
        right: 0;
        top: 0;
        height: 100vh;
        height: 100dvh;
        width: 100vw;
        background: #fff;
        z-index: 9998;
        display: flex;
        flex-direction: column;
        font-family: inherit;
    ` : `
        position: fixed;
        right: 0;
        top: 0;
        height: 100vh;
        width: 380px;
        background: #fff;
        box-shadow: -4px 0 16px rgba(0,0,0,0.15);
        z-index: 9998;
        display: flex;
        flex-direction: column;
        font-family: inherit;
    `;

    panel.innerHTML = `
        <!-- Drag handle (desktop only — resizing a full-screen mobile panel makes no sense) -->
        ${is_mobile_view ? '' : '<div id="ai-drag-handle" style="position:absolute;left:0;top:0;width:5px;height:100%;cursor:col-resize;background:transparent;z-index:1;"></div>'}

        <!-- Header -->
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #e2e8f0;background:#5b8def;color:white;flex-shrink:0;">
            <span style="font-weight:600;font-size:15px;min-width:0;">${_ai_logo_html(20)} ${ai_title}</span>
            <div style="display:flex;align-items:center;gap:10px;flex-shrink:0;">
                ${role_dropdown_html}
                <button id="ai-panel-close" style="background:none;border:none;color:white;font-size:20px;cursor:pointer;line-height:1;padding:0 4px;">×</button>
            </div>
        </div>

        <!-- Chat history -->
        <div class="ai-chat-history" style="flex:1;overflow-y:auto;padding:12px;overscroll-behavior:contain;"></div>

        <!-- Bottom area -->
        <div style="border-top:1px solid #e2e8f0;padding:10px;flex-shrink:0;background:#f8fafc;">

            <!-- File upload -->
            <div style="margin-bottom:8px;">
                <input type="file" class="ai-file-input" accept="image/*,application/pdf"
                    style="display:block;width:100%;padding:4px;border:1px solid #ddd;border-radius:4px;font-size:11px;background:white;">
                <div style="display:flex;align-items:center;gap:6px;margin-top:4px;">
                    <div class="ai-file-preview" style="font-size:11px;color:#888;flex:1;"></div>
                    <button class="ai-file-remove btn btn-xs btn-danger" style="display:none;">Remove</button>
                </div>
            </div>

            <!-- Quick prompts (collapsed by default) -->
            ${SHOW_QUICK_PROMPTS ? `
            <div style="margin-bottom:8px;">
                <div class="ai-quick-prompts-toggle" style="font-size:11px;color:#888;margin-bottom:4px;cursor:pointer;user-select:none;display:flex;align-items:center;gap:4px;">
                    <span class="ai-quick-prompts-arrow">▾</span> Quick prompts
                </div>
                <div class="ai-quick-prompts-list" style="display:none;flex-wrap:wrap;">${prompt_buttons}</div>
            </div>` : ''}

            <!-- Message input + Mic + Send (mic only if Sarvam speech-to-text is configured) -->
            <div style="display:flex;gap:6px;align-items:stretch;">
                ${frappe.boot.enable_speech_to_text ? `
                <div style="position:relative;display:inline-block;">
                    <div class="ai-mic-tooltip" style="display:none;position:absolute;bottom:calc(100% + 8px);${is_mobile_view ? 'left:0;' : 'left:50%;transform:translateX(-50%);'}background:#1e293b;color:#fff;font-size:11px;padding:5px 10px;border-radius:6px;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,0.2);z-index:10;">
                        🎙️ Listening... tap to stop
                        <div style="position:absolute;top:100%;left:50%;transform:translateX(-50%);border:5px solid transparent;border-top-color:#1e293b;"></div>
                    </div>
                    <button class="ai-mic-btn btn btn-sm" title="Voice input" style="white-space:nowrap;height:38px;">🎤</button>
                </div>` : ''}
                <input type="text" class="ai-message-input form-control" placeholder="Type your message..." style="flex:1;font-size:14px;height:38px;border:1.5px solid #b0b7c3;color:#333;">
                <button class="ai-send-btn btn btn-primary btn-sm" style="white-space:nowrap;height:38px;">Send</button>
            </div>
        </div>
    `;
    
    if (is_mobile_view) {
        document.documentElement.appendChild(panel);
    } else {
        document.body.appendChild(panel);
    }

    // Push all content left using body (skip on mobile — the full-screen
    // panel already covers everything, no margin needed).
    if (!is_mobile_view) {
        document.body.style.transition = 'margin-right 0.2s';
        document.body.style.marginRight = '380px';
    }


    let _prevBodyOverflow = '';

    if (is_mobile_view) {
        _prevBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';

        const syncPanelHeight = function () {
            const vh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
            panel.style.height = vh + 'px';
        };
        syncPanelHeight();
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', syncPanelHeight);
        }
        window.addEventListener('resize', syncPanelHeight);
        window.addEventListener('orientationchange', syncPanelHeight);

        const msgInputEl = panel.querySelector('.ai-message-input');
        if (msgInputEl) {
            msgInputEl.addEventListener('focus', function () {
                setTimeout(function () {
                    syncPanelHeight();
                    msgInputEl.scrollIntoView({ block: 'end' });
                }, 300);
            });
        }
    }

    // Close button
    // Drag to resize (desktop only — see the drag handle guard above)
    const dragHandle = document.getElementById('ai-drag-handle');
    let isDragging = false;
    let startX, startWidth;

    if (dragHandle) {
        dragHandle.addEventListener('mousedown', function (e) {
            isDragging = true;
            startX = e.clientX;
            startWidth = panel.offsetWidth;
            document.body.style.userSelect = 'none';
        });
    }

    function onMouseMove(e) {
        if (!isDragging) return;
        const diff = startX - e.clientX;
        const newWidth = Math.min(Math.max(300, startWidth + diff), window.innerWidth * 0.6);
        panel.style.width = newWidth + 'px';
        document.body.style.marginRight = newWidth + 'px';
    }

    function onMouseUp() {
        isDragging = false;
        document.body.style.userSelect = '';
    }

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    // Quick prompts toggle — collapsed by default (see the ai-quick-prompts-list
    // display:none above); click the label to expand/collapse, arrow flips too.
    const quickPromptsToggle = panel.querySelector('.ai-quick-prompts-toggle');
    const quickPromptsList = panel.querySelector('.ai-quick-prompts-list');
    const quickPromptsArrow = panel.querySelector('.ai-quick-prompts-arrow');
    if (quickPromptsToggle && quickPromptsList && quickPromptsArrow) {
        quickPromptsToggle.addEventListener('click', function () {
            const isOpen = quickPromptsList.style.display !== 'none';
            quickPromptsList.style.display = isOpen ? 'none' : 'flex';
            quickPromptsArrow.textContent = isOpen ? '▾' : '▴';
        });
    }

    document.getElementById('ai-panel-close').addEventListener('click', function () {
        document.body.classList.add('ai-modal-active');

        panel.style.zIndex = '1';
        frappe.confirm(
            __('Close the AI Chat? Your current conversation will be cleared.'),
            function () {
                // Yes — proceed with closing
                document.body.classList.remove('ai-modal-active');
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
                panel.remove();
                document.body.style.marginRight = '';
                document.body.style.transition = '';
                if (is_mobile_view) {
                    document.body.style.overflow = _prevBodyOverflow;
                }
                const floatBtn = document.getElementById('ai-floating-btn');
                if (floatBtn) floatBtn.style.display = 'flex';
            },
            function () {
                // No — dialog dismisses, panel stays open untouched
                document.body.classList.remove('ai-modal-active');
                panel.style.zIndex = '9998';
            }
        );
    });

    // Role dropdown — manual pick/change, persisted per-user for next open.
    const roleDropdownEl = panel.querySelector('.ai-role-dropdown');
    if (roleDropdownEl) {
        roleDropdownEl.addEventListener('change', function () {
            selected_ai_role = this.value;
            localStorage.setItem(_role_storage_key, selected_ai_role);
        });
    }

    // Conversation history
    let conversation_history = [];

    let is_ai_busy = false;

    // Doc context — starts from current route, can be updated after doc creation
    const _initCtx = frappe.get_route();
    let active_doctype = (_initCtx && _initCtx[0] === 'Form' && _initCtx[1]) ? _initCtx[1] : null;
    let active_docname = (_initCtx && _initCtx[0] === 'Form' && _initCtx[2]) ? _initCtx[2] : null;

    function sendMessage(message, silent, retryOf) {
        if (is_ai_busy) return;
        is_ai_busy = true;
        const sendBtn = panel.querySelector('.ai-send-btn');
        if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = '...'; }

        const history = panel.querySelector('.ai-chat-history');
        let msg_id;

        if (retryOf) {
            msg_id = retryOf;
            const existingEl = panel.querySelector(`[data-msg-id="${msg_id}"]`);
            if (existingEl) {
                existingEl.innerHTML = `<span style="background:#f0f0f0;padding:6px 10px;border-radius:10px;display:inline-block;">Thinking...</span>`;
            }
            conversation_history.splice(-2, 2);
        } else {
            msg_id = 'msg_' + Date.now();
            let userHtml = '';
            if (!silent) {
                // Copy stays on every past message — harmless regardless of
                // position. Retry only ever shows on the latest one — retrying
                // an older message would leave everything sent after it stale,
                // so drop it from whichever message previously had it.
                const prevRetryBtn = history.querySelector('.ai-retry-btn');
                if (prevRetryBtn) prevRetryBtn.remove();

                userHtml = `<div style="text-align:right;margin:5px 0 1px;"><span style="background:#d4f1c0;padding:6px 10px;border-radius:10px;display:inline-block;max-width:85%;word-break:break-word;">${message}</span></div>
                <div class="ai-msg-actions" style="text-align:right;margin:-4px 0 2px;">
                    <button class="ai-copy-btn" style="background:transparent !important;box-shadow:none;border:none;cursor:pointer;font-size:9px;color:#333;padding:1px 4px;display:inline-flex;align-items:center;gap:3px;">
                        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                        Copy
                    </button>
                    <button class="ai-retry-btn" style="background:transparent !important;box-shadow:none;border:none;cursor:pointer;font-size:9px;color:#333;padding:1px 4px;display:inline-flex;align-items:center;gap:3px;">
                        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                        Retry
                    </button>
                </div>`;
            }
            history.innerHTML += userHtml + `<div data-msg-id="${msg_id}" style="text-align:left;margin:5px 0;"><span style="background:#f0f0f0;padding:6px 10px;border-radius:10px;display:inline-block;">Thinking...</span></div>`;

            if (!silent) {
                const actionsRows = history.querySelectorAll('.ai-msg-actions');
                const actionsEl = actionsRows[actionsRows.length - 1];
                if (actionsEl) {
                    actionsEl.querySelector('.ai-copy-btn').addEventListener('click', function () {
                        navigator.clipboard.writeText(message).then(() => {
                            // innerHTML, not textContent — the button now contains an SVG
                            // icon too; textContent would wipe it out permanently since
                            // setting it replaces ALL child nodes, not just the text.
                            const original = this.innerHTML;
                            this.innerHTML = 'Copied';
                            setTimeout(() => { this.innerHTML = original; }, 1200);
                        });
                    });
                    actionsEl.querySelector('.ai-retry-btn').addEventListener('click', function () {
                        sendMessage(message, false, msg_id);
                    });
                }
            }
        }
        history.scrollTop = history.scrollHeight;

        const msgInput = panel.querySelector('.ai-message-input');
        if (!silent && !retryOf) msgInput.value = '';

        const current_doctype = active_doctype;
        const current_docname = active_docname;

        frappe.call({
            method: 'pilot_assist.api.ai_api.ask_document',
            args: {
                message: message,
                doctype: current_doctype,
                docname: current_docname,
                image_data: image_data,
                image_mime: image_mime,
                history: JSON.stringify(conversation_history.slice(-6)),
                selected_role: selected_ai_role
            },
            callback: function (r) {
                const response_text = r.message || "No response received.";
                let el = panel.querySelector(`[data-msg-id="${msg_id}"]`);

                if (response_text.startsWith("PI_PREVIEW:")) {
                    // Show purchase invoice two-column preview card
                    try {
                        const pi_data = JSON.parse(response_text.replace("PI_PREVIEW:", ""));
                        const mapped   = pi_data.mapped_data || {};
                        const items    = Array.isArray(mapped.items) ? mapped.items : [];
                        const taxes    = Array.isArray(mapped.taxes) ? mapped.taxes : [];
                        const pid      = 'pip' + Date.now();

                        // Separate regular items from packing/freight/service charges
                        const _chargeKws = ['packing','freight','transport','charge','charges','delivery','handling','loading','unloading','service','other','miscellaneous'];
                        const _isCharge  = it => _chargeKws.some(kw => (it.item_name||it.item_code||it.description||'').toLowerCase().includes(kw));
                        const regularItems = items.filter(it => !_isCharge(it));
                        const chargeItems  = items.filter(it => _isCharge(it));

                        if (!is_mobile_view) {
                            panel.style.width = Math.min(760, window.innerWidth * 0.85) + 'px';
                            document.body.style.marginRight = panel.style.width;
                        }

                        // ── LEFT: every field extracted from the image ───────────────────────
                        const scalarRows = Object.entries(mapped)
                            .filter(([k, v]) => !['items','taxes','doctype','name'].includes(k) && v !== null && v !== '' && typeof v !== 'object')
                            .map(([k, v]) => `<tr>
                                <td style="padding:2px 6px;color:#64748b;font-size:11px;white-space:nowrap;vertical-align:top;">${k}</td>
                                <td style="padding:2px 6px;font-size:11px;word-break:break-word;">${v}</td>
                            </tr>`).join('');

                        const leftItemRows = items.map((item, i) => {
                            const chips = Object.entries(item)
                                .filter(([k, v]) => v !== null && v !== '' && v !== undefined)
                                .map(([k, v]) => `<span style="display:inline-block;margin:1px 2px 1px 0;font-size:10px;background:#f1f5f9;padding:1px 5px;border-radius:3px;"><b>${k}:</b> ${v}</span>`)
                                .join('');
                            return `<div style="padding:4px 0;border-bottom:1px solid #f1f5f9;">
                                <div style="font-size:10px;color:#94a3b8;margin-bottom:2px;">Row ${i+1}</div>
                                <div>${chips}</div>
                            </div>`;
                        }).join('');

                        const leftTaxRows = taxes.map(t => {
                            const chips = Object.entries(t)
                                .filter(([k, v]) => v !== null && v !== '')
                                .map(([k, v]) => `<span style="display:inline-block;margin:1px 2px 1px 0;font-size:10px;background:#fef9c3;padding:1px 5px;border-radius:3px;"><b>${k}:</b> ${v}</span>`)
                                .join('');
                            return `<div style="padding:4px 0;border-bottom:1px solid #f1f5f9;">${chips}</div>`;
                        }).join('');

                        const imgSrc = image_data ? `data:${image_mime||'image/jpeg'};base64,${image_data}` : '';
                        const isPdf  = image_mime === 'application/pdf';
                        const leftPanel = `
                            <div id="${pid}-left" style="flex:1;min-width:0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px;overflow:auto;max-height:520px;">
                                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;border-bottom:1px solid #e2e8f0;padding-bottom:4px;">
                                    <span id="${pid}-left-title" style="font-weight:600;color:#334155;font-size:12px;">${isPdf ? '📄 Uploaded PDF' : '📷 Uploaded Image'}</span>
                                    ${imgSrc ? `<button id="${pid}-toggle-left"
                                        style="font-size:10px;padding:2px 8px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;color:#4f46e5;cursor:pointer;">
                                        🔍 View Extracted
                                    </button>` : ''}
                                </div>
                                <div id="${pid}-extracted" style="display:none;">
                                    <table style="width:100%;border-collapse:collapse;">${scalarRows}</table>
                                    ${items.length ? `<div style="font-weight:600;font-size:11px;color:#334155;margin:8px 0 4px;">Items (${items.length})</div>${leftItemRows}` : ''}
                                    ${taxes.length ? `<div style="font-weight:600;font-size:11px;color:#334155;margin:8px 0 4px;">Taxes</div>${leftTaxRows}` : ''}
                                </div>
                                <div id="${pid}-imgview" style="display:block;text-align:center;">
                                    ${isPdf ? `
                                    <div style="overflow:auto;border:1px solid #e2e8f0;border-radius:6px;">
                                        <iframe src="${imgSrc}" style="width:100%;height:480px;border:none;"></iframe>
                                    </div>` : imgSrc ? `
                                    <div style="display:flex;justify-content:center;gap:8px;margin-bottom:6px;">
                                        <button id="${pid}-rot-l" title="Rotate Left"
                                            style="padding:3px 10px;font-size:13px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;cursor:pointer;">↺</button>
                                        <button id="${pid}-rot-r" title="Rotate Right"
                                            style="padding:3px 10px;font-size:13px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;cursor:pointer;">↻</button>
                                    </div>
                                    <div style="overflow:auto;">
                                        <img id="${pid}-inv-img" src="${imgSrc}"
                                            style="max-width:100%;border-radius:6px;cursor:zoom-in;transition:transform 0.25s ease;transform-origin:center center;"
                                            title="Click to fullscreen">
                                    </div>` : ''}
                                </div>
                            </div>`;

                        // ── RIGHT: editable PI form ──────────────────────────────────────────
                        const supplier      = mapped.supplier || mapped.supplier_name || '';
                        const billNo        = mapped.bill_no  || '';
                        const billDate      = mapped.bill_date || mapped.posting_date || '';
                        const currency      = mapped.currency  || 'INR';
                        const supplierGstin = mapped.supplier_gstin || '';
                        const placeOfSupply = mapped.place_of_supply || '';

                        const subtotal      = regularItems.reduce((s, it) => s + parseFloat(it.qty||1) * parseFloat(it.rate||0), 0);
                        const chargesTotal  = chargeItems.reduce((s, it) => s + parseFloat(it.qty||1) * parseFloat(it.rate||0), 0);
                        const taxableBase   = subtotal + chargesTotal;
                        const grandTotal    = parseFloat(mapped.grand_total || taxableBase);
                        const taxAmt        = grandTotal - taxableBase;
                        const taxRate       = taxableBase > 0 ? Math.round(taxAmt / taxableBase * 100) : 0;

                        const inpStyle = 'width:100%;font-size:11px;border:1px solid #e2e8f0;border-radius:4px;padding:3px 5px;box-sizing:border-box;';
                        const mkInp = (id, label, val, span2) => `
                            <div style="grid-column:${span2 ? 'span 2' : 'span 1'};">
                                <div style="font-size:10px;color:#64748b;margin-bottom:2px;">${label}</div>
                                <input id="${pid}-${id}" value="${(val||'').toString().replace(/"/g,'&quot;')}" style="${inpStyle}">
                            </div>`;

                        const editableItemRows = regularItems.map((item) => {
                            const i    = items.indexOf(item);
                            const qty  = parseFloat(item.qty  || 1);
                            const rate = parseFloat(item.rate || 0);
                            const amt  = qty * rate;
                            const nm   = (item.item_name || item.item_code || item.description || '').replace(/"/g,'&quot;');
                            return `<tr class="${pid}-row" data-idx="${i}" data-qty="${qty}" data-rate="${rate}" style="border-bottom:1px solid #f1f5f9;">
                                <td style="padding:2px 3px;">
                                    <input data-f="item_name" value="${nm}"
                                        style="width:100%;font-size:11px;border:1px solid #e2e8f0;border-radius:3px;padding:2px 4px;box-sizing:border-box;">
                                </td>
                                <td style="padding:2px 6px;font-size:11px;text-align:center;">${qty}</td>
                                <td style="padding:2px 6px;font-size:11px;text-align:right;">${rate.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                <td class="${pid}-amt" style="padding:2px 6px;font-size:11px;text-align:right;white-space:nowrap;">
                                    ${amt.toLocaleString('en-IN',{minimumFractionDigits:2})}
                                </td>
                            </tr>`;
                        }).join('');

                        const chargeRows = chargeItems.map((item) => {
                            const i   = items.indexOf(item);
                            const amt = parseFloat(item.qty||1) * parseFloat(item.rate||0);
                            const nm  = (item.item_name || item.item_code || item.description || '').replace(/</g,'&lt;');
                            return `<tr class="${pid}-charge-row" data-idx="${i}">
                                <td colspan="3" style="padding:3px 6px;font-size:11px;color:#94a3b8;font-style:italic;">${nm}</td>
                                <td style="padding:2px 3px;text-align:right;">
                                    <input class="${pid}-charge-amt" type="number" value="${amt.toFixed(2)}" min="0" step="0.01"
                                        style="width:80px;font-size:11px;border:1px solid #e2e8f0;border-radius:3px;padding:2px 3px;text-align:right;color:#64748b;">
                                </td>
                            </tr>`;
                        }).join('');

                        const rightPanel = `
                            <div style="background:#fff;border:1px solid #4f46e5;border-radius:8px;padding:10px;overflow:auto;max-height:520px;">
                                <div style="font-weight:600;color:#4f46e5;font-size:12px;margin-bottom:8px;border-bottom:1px solid #e2e8f0;padding-bottom:4px;">
                                    📄 Purchase Invoice
                                    <span style="font-size:10px;color:#94a3b8;font-weight:400;margin-left:6px;">Edit fields, then click Apply</span>
                                </div>
                                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;">
                                    ${mkInp('supplier','Supplier', supplier, true)}
                                    ${mkInp('bill_no','Invoice No', billNo)}
                                    ${mkInp('bill_date','Date', billDate)}
                                    ${mkInp('supplier_gstin','Supplier GSTIN', supplierGstin)}
                                    ${mkInp('place_of_supply','Place of Supply', placeOfSupply)}
                                </div>
                                <table style="width:100%;border-collapse:collapse;margin-bottom:8px;">
                                    <thead>
                                        <tr style="background:#f8fafc;border-bottom:2px solid #e2e8f0;">
                                            <th style="padding:4px 6px;font-size:11px;text-align:left;font-weight:600;color:#334155;">Item</th>
                                            <th style="padding:4px 6px;font-size:11px;text-align:center;font-weight:600;color:#334155;">Qty</th>
                                            <th style="padding:4px 6px;font-size:11px;text-align:right;font-weight:600;color:#334155;">Rate</th>
                                            <th style="padding:4px 6px;font-size:11px;text-align:right;font-weight:600;color:#334155;">Amt</th>
                                        </tr>
                                    </thead>
                                    <tbody>${editableItemRows}</tbody>
                                    <tfoot>
                                        <tr style="border-top:1px solid #e2e8f0;">
                                            <td colspan="3" style="padding:3px 6px;font-size:11px;color:#64748b;">Subtotal</td>
                                            <td id="${pid}-sub" style="padding:3px 6px;font-size:11px;text-align:right;">${subtotal.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                        </tr>
                                        ${chargeRows}
                                        ${taxAmt > 0.01 ? `<tr>
                                            <td colspan="3" style="padding:3px 6px;font-size:11px;color:#64748b;">Tax${taxRate ? ' ('+taxRate+'%)' : ''}</td>
                                            <td id="${pid}-tax" style="padding:3px 6px;font-size:11px;text-align:right;">${taxAmt.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                        </tr>` : ''}
                                        <tr style="border-top:2px solid #4f46e5;background:#f8fafc;">
                                            <td colspan="3" style="padding:5px 6px;font-size:12px;font-weight:700;color:#1e293b;">Grand Total (${currency})</td>
                                            <td id="${pid}-gt" style="padding:5px 6px;font-size:13px;font-weight:700;color:#4f46e5;text-align:right;">₹ ${grandTotal.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                        </tr>
                                    </tfoot>
                                </table>
                            </div>`;

                        const card = `
                            <div style="background:#fff;border-radius:8px;padding:4px 0;" id="${pid}">
                                <div style="display:flex;${is_mobile_view ? 'flex-direction:column;' : ''}gap:10px;align-items:flex-start;margin-bottom:8px;">
                                    ${leftPanel}
                                    <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;">
                                        ${rightPanel}
                                        <button id="${pid}-apply"
                                            style="display:none;width:100%;padding:7px;font-size:12px;font-weight:600;background:#e0e7ff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:6px;cursor:pointer;">
                                            🔄 Apply Changes
                                        </button>
                                    </div>
                                </div>
                                <button class="ai-create-pi-btn"
                                    data-pi='${JSON.stringify(pi_data).replace(/'/g, "&#39;")}'
                                    style="width:100%;padding:10px;font-size:13px;font-weight:600;background:#4f46e5;color:#fff;border:none;border-radius:6px;cursor:pointer;">
                                    ✅ Create Purchase Invoice
                                </button>
                            </div>`;
                        if (el) el.innerHTML = card;

                        const wrapper = document.getElementById(pid);

                        // Left panel toggle — switch between extracted fields and uploaded image
                        const toggleBtn = wrapper.querySelector(`#${pid}-toggle-left`);
                        if (toggleBtn) {
                            let showingImage = true;
                            toggleBtn.addEventListener('click', () => {
                                showingImage = !showingImage;
                                wrapper.querySelector(`#${pid}-extracted`).style.display = showingImage ? 'none' : 'block';
                                wrapper.querySelector(`#${pid}-imgview`).style.display   = showingImage ? 'block' : 'none';
                                wrapper.querySelector(`#${pid}-left-title`).textContent  = showingImage ? (isPdf ? '📄 Uploaded PDF' : '📷 Uploaded Image') : '🔍 Extracted from Image';
                                toggleBtn.textContent = showingImage ? '🔍 View Extracted' : (isPdf ? '📄 View PDF' : '📷 View Image');
                            });
                            // Rotation controls
                            let _rot = 0;
                            const invImg = wrapper.querySelector(`#${pid}-inv-img`);
                            const rotL   = wrapper.querySelector(`#${pid}-rot-l`);
                            const rotR   = wrapper.querySelector(`#${pid}-rot-r`);
                            if (invImg) {
                                if (rotL) rotL.addEventListener('click', () => { _rot -= 90; invImg.style.transform = `rotate(${_rot}deg)`; });
                                if (rotR) rotR.addEventListener('click', () => { _rot += 90; invImg.style.transform = `rotate(${_rot}deg)`; });
                                invImg.addEventListener('click', () => openAIImageLightbox(imgSrc));
                            }
                        }

                        // Auto-recalc row amounts + totals when qty/rate/charge changes
                        function recalcPITotals() {
                            let newSub = 0;
                            wrapper.querySelectorAll(`.${pid}-row`).forEach(tr => {
                                const q = parseFloat(tr.dataset.qty)  || 0;
                                const r = parseFloat(tr.dataset.rate) || 0;
                                const a = q * r;
                                tr.querySelector(`.${pid}-amt`).textContent = a.toLocaleString('en-IN',{minimumFractionDigits:2});
                                newSub += a;
                            });
                            let newCharges = 0;
                            wrapper.querySelectorAll(`.${pid}-charge-amt`).forEach(inp => {
                                newCharges += parseFloat(inp.value) || 0;
                            });
                            const newTaxableBase = newSub + newCharges;
                            const newTax = Math.round(newTaxableBase * taxRate / 100 * 100) / 100;
                            wrapper.querySelector(`#${pid}-sub`).textContent = newSub.toLocaleString('en-IN',{minimumFractionDigits:2});
                            const taxEl = wrapper.querySelector(`#${pid}-tax`);
                            if (taxEl) taxEl.textContent = newTax.toLocaleString('en-IN',{minimumFractionDigits:2});
                            wrapper.querySelector(`#${pid}-gt`).textContent = '₹ ' + (newTaxableBase + newTax).toLocaleString('en-IN',{minimumFractionDigits:2});
                        }
                        const applyBtn = wrapper.querySelector(`#${pid}-apply`);
                        const showApply = () => { applyBtn.style.display = 'block'; };

                        wrapper.querySelectorAll(`.${pid}-charge-amt`).forEach(i => {
                            i.addEventListener('input', () => { recalcPITotals(); showApply(); });
                        });
                        wrapper.querySelectorAll(`#${pid}-supplier,#${pid}-bill_no,#${pid}-bill_date,#${pid}-supplier_gstin,#${pid}-place_of_supply`).forEach(i => {
                            i.addEventListener('input', showApply);
                        });
                        wrapper.querySelectorAll('[data-f="item_name"]').forEach(i => i.addEventListener('input', showApply));

                        // Apply — read edited values, update data-pi on Create button
                        applyBtn.addEventListener('click', function() {
                            const newMapped = Object.assign({}, pi_data.mapped_data);
                            newMapped.supplier        = wrapper.querySelector(`#${pid}-supplier`).value;
                            newMapped.bill_no         = wrapper.querySelector(`#${pid}-bill_no`).value;
                            newMapped.bill_date       = wrapper.querySelector(`#${pid}-bill_date`).value;
                            newMapped.supplier_gstin  = wrapper.querySelector(`#${pid}-supplier_gstin`).value;
                            newMapped.place_of_supply = wrapper.querySelector(`#${pid}-place_of_supply`).value;

                            const newItems = [];
                            wrapper.querySelectorAll(`.${pid}-row`).forEach(tr => {
                                const orig = (pi_data.mapped_data.items || [])[parseInt(tr.dataset.idx)] || {};
                                newItems.push(Object.assign({}, orig, {
                                    item_name: tr.querySelector('[data-f="item_name"]').value,
                                    qty:  parseFloat(tr.dataset.qty)  || 1,
                                    rate: parseFloat(tr.dataset.rate) || 0,
                                }));
                            });
                            wrapper.querySelectorAll(`.${pid}-charge-row`).forEach(tr => {
                                const orig = (pi_data.mapped_data.items || [])[parseInt(tr.dataset.idx)] || {};
                                const editedAmt = parseFloat(tr.querySelector(`.${pid}-charge-amt`).value) || 0;
                                newItems.push(Object.assign({}, orig, { qty: 1, rate: editedAmt }));
                            });
                            newMapped.items = newItems;
                            const newRegSub  = newItems.filter(it => !_isCharge(it)).reduce((s, it) => s + it.qty * it.rate, 0);
                            const newChgSub  = newItems.filter(it => _isCharge(it)).reduce((s, it) => s + it.qty * it.rate, 0);
                            const newBase    = newRegSub + newChgSub;
                            newMapped.grand_total = Math.round((newBase + newBase * taxRate / 100) * 100) / 100;

                            const newPiData = Object.assign({}, pi_data, { mapped_data: newMapped });
                            wrapper.querySelector('.ai-create-pi-btn').setAttribute('data-pi', JSON.stringify(newPiData).replace(/'/g,"&#39;"));

                            this.textContent = '✅ Applied!';
                            setTimeout(() => { this.style.display = 'none'; this.textContent = '🔄 Apply Changes'; }, 1200);
                        });

                        // Bind create button
                        wrapper.querySelector('.ai-create-pi-btn').addEventListener('click', function () {
                            const data = this.getAttribute('data-pi');
                            this.disabled = true;
                            this.textContent = 'Creating...';
                            const btn = this;
                            frappe.call({
                                method: 'pilot_assist.api.ai_api.create_purchase_invoice_from_data',
                                args: { data: data },
                                callback: function (res) {
                                    if (res.message) {
                                        const msg = res.message;

                                        // Items not found in ERPNext Item master
                                        if (msg.status === 'items_not_found') {
                                            btn.disabled = false;
                                            btn.textContent = 'Create Purchase Invoice';
                                            const itemList = msg.not_found.map(i => `<strong>${i}</strong>`).join(', ');
                                            const warnDiv = document.createElement('div');
                                            warnDiv.style.cssText = 'margin-top:8px;padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:12px;color:#856404;';
                                            warnDiv.innerHTML = `⚠️ Could not create Purchase Invoice.<br>The following could not be matched in ERPNext:<br>${itemList}<br><br>Please add them first (or correct the name/spelling), then try again.`;
                                            el.querySelector('div').appendChild(warnDiv);
                                            return;
                                        }

                                        // Success
                                        const { name, url } = msg;
                                        btn.remove();
                                        const successDiv = document.createElement('div');
                                        successDiv.style.cssText = 'margin-top:8px;padding:8px;background:#d4f1c0;border-radius:6px;font-size:12px;';
                                        successDiv.innerHTML = `✅ Created: <strong>${name}</strong><br><a href="${url}" target="_blank" style="color:#4f46e5;">Click to open →</a>`;
                                        el.querySelector('div').appendChild(successDiv);

                                        // Set doc context for follow-up questions
                                        active_doctype = 'Purchase Invoice';
                                        active_docname = name;
                                        conversation_history.push({
                                            role: 'model',
                                            content: `Purchase Invoice ${name} has been created successfully from the uploaded image. You can now ask questions about this document.`
                                        });

                                        // AI asks follow-up questions automatically (silent — no user bubble)
                                        setTimeout(() => {
                                            sendMessage(`Purchase Invoice ${name} was just created. Review the document and ask me 3 short follow-up questions about what I should verify or do next — like submitting, checking supplier, GST, or any missing fields. Keep it brief.`, true);
                                        }, 500);
                                    }
                                }
                            });
                        });
                    } catch(e) {
                        if (el) el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">${response_text}</div>`;
                    }
                } else if (response_text.startsWith("QUOTATION_PREVIEW:")) {
                    // Show quotation two-column preview card — mirrors the
                    // PI_PREVIEW card above (same item-matching/edit/apply
                    // flow), retargeted to Quotation's own fields.
                    try {
                        const q_data = JSON.parse(response_text.replace("QUOTATION_PREVIEW:", ""));
                        const mapped = q_data.mapped_data || {};
                        const items  = Array.isArray(mapped.items) ? mapped.items : [];
                        const taxes  = Array.isArray(mapped.taxes) ? mapped.taxes : [];
                        const qid    = 'qtn' + Date.now();

                        const _qChargeKws = ['packing','freight','transport','charge','charges','delivery','handling','loading','unloading','service','other','miscellaneous'];
                        const _qIsCharge  = it => _qChargeKws.some(kw => (it.item_name||it.item_code||it.description||'').toLowerCase().includes(kw));
                        const regularItems = items.filter(it => !_qIsCharge(it));
                        const chargeItems  = items.filter(it => _qIsCharge(it));

                        if (!is_mobile_view) {
                            panel.style.width = Math.min(760, window.innerWidth * 0.85) + 'px';
                            document.body.style.marginRight = panel.style.width;
                        }

                        // ── LEFT: every field extracted from the image ───────────────────────
                        const scalarRows = Object.entries(mapped)
                            .filter(([k, v]) => !['items','taxes','doctype','name'].includes(k) && v !== null && v !== '' && typeof v !== 'object')
                            .map(([k, v]) => `<tr>
                                <td style="padding:2px 6px;color:#64748b;font-size:11px;white-space:nowrap;vertical-align:top;">${k}</td>
                                <td style="padding:2px 6px;font-size:11px;word-break:break-word;">${v}</td>
                            </tr>`).join('');

                        const leftItemRows = items.map((item, i) => {
                            const chips = Object.entries(item)
                                .filter(([k, v]) => v !== null && v !== '' && v !== undefined)
                                .map(([k, v]) => `<span style="display:inline-block;margin:1px 2px 1px 0;font-size:10px;background:#f1f5f9;padding:1px 5px;border-radius:3px;"><b>${k}:</b> ${v}</span>`)
                                .join('');
                            return `<div style="padding:4px 0;border-bottom:1px solid #f1f5f9;">
                                <div style="font-size:10px;color:#94a3b8;margin-bottom:2px;">Row ${i+1}</div>
                                <div>${chips}</div>
                            </div>`;
                        }).join('');

                        const leftTaxRows = taxes.map(t => {
                            const chips = Object.entries(t)
                                .filter(([k, v]) => v !== null && v !== '')
                                .map(([k, v]) => `<span style="display:inline-block;margin:1px 2px 1px 0;font-size:10px;background:#fef9c3;padding:1px 5px;border-radius:3px;"><b>${k}:</b> ${v}</span>`)
                                .join('');
                            return `<div style="padding:4px 0;border-bottom:1px solid #f1f5f9;">${chips}</div>`;
                        }).join('');

                        const qImgSrc = image_data ? `data:${image_mime||'image/jpeg'};base64,${image_data}` : '';
                        const qIsPdf  = image_mime === 'application/pdf';
                        const leftPanel = `
                            <div id="${qid}-left" style="flex:1;min-width:0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px;overflow:auto;max-height:520px;">
                                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;border-bottom:1px solid #e2e8f0;padding-bottom:4px;">
                                    <span id="${qid}-left-title" style="font-weight:600;color:#334155;font-size:12px;">${qIsPdf ? '📄 Uploaded PDF' : '📷 Uploaded Image'}</span>
                                    ${qImgSrc ? `<button id="${qid}-toggle-left"
                                        style="font-size:10px;padding:2px 8px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;color:#4f46e5;cursor:pointer;">
                                        🔍 View Extracted
                                    </button>` : ''}
                                </div>
                                <div id="${qid}-extracted" style="display:none;">
                                    <table style="width:100%;border-collapse:collapse;">${scalarRows}</table>
                                    ${items.length ? `<div style="font-weight:600;font-size:11px;color:#334155;margin:8px 0 4px;">Items (${items.length})</div>${leftItemRows}` : ''}
                                    ${taxes.length ? `<div style="font-weight:600;font-size:11px;color:#334155;margin:8px 0 4px;">Taxes</div>${leftTaxRows}` : ''}
                                </div>
                                <div id="${qid}-imgview" style="display:block;text-align:center;">
                                    ${qIsPdf ? `
                                    <div style="overflow:auto;border:1px solid #e2e8f0;border-radius:6px;">
                                        <iframe src="${qImgSrc}" style="width:100%;height:480px;border:none;"></iframe>
                                    </div>` : qImgSrc ? `
                                    <div style="display:flex;justify-content:center;gap:8px;margin-bottom:6px;">
                                        <button id="${qid}-rot-l" title="Rotate Left"
                                            style="padding:3px 10px;font-size:13px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;cursor:pointer;">↺</button>
                                        <button id="${qid}-rot-r" title="Rotate Right"
                                            style="padding:3px 10px;font-size:13px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;cursor:pointer;">↻</button>
                                    </div>
                                    <div style="overflow:auto;">
                                        <img id="${qid}-inv-img" src="${qImgSrc}"
                                            style="max-width:100%;border-radius:6px;cursor:zoom-in;transition:transform 0.25s ease;transform-origin:center center;"
                                            title="Click to fullscreen">
                                    </div>` : ''}
                                </div>
                            </div>`;

                        // ── RIGHT: editable Quotation form ───────────────────────────────────
                        const customer      = mapped.customer_name || mapped.party_name || '';
                        const txnDate       = mapped.transaction_date || '';
                        const validTill     = mapped.valid_till || '';
                        const currency      = mapped.currency || 'INR';

                        const subtotal      = regularItems.reduce((s, it) => s + parseFloat(it.qty||1) * parseFloat(it.rate||0), 0);
                        const chargesTotal  = chargeItems.reduce((s, it) => s + parseFloat(it.qty||1) * parseFloat(it.rate||0), 0);
                        const taxableBase   = subtotal + chargesTotal;
                        const grandTotal    = parseFloat(mapped.grand_total || taxableBase);
                        const taxAmt        = grandTotal - taxableBase;
                        const taxRate       = taxableBase > 0 ? Math.round(taxAmt / taxableBase * 100) : 0;

                        const inpStyle = 'width:100%;font-size:11px;border:1px solid #e2e8f0;border-radius:4px;padding:3px 5px;box-sizing:border-box;';
                        const mkInp = (id, label, val, span2) => `
                            <div style="grid-column:${span2 ? 'span 2' : 'span 1'};">
                                <div style="font-size:10px;color:#64748b;margin-bottom:2px;">${label}</div>
                                <input id="${qid}-${id}" value="${(val||'').toString().replace(/"/g,'&quot;')}" style="${inpStyle}">
                            </div>`;

                        const editableItemRows = regularItems.map((item) => {
                            const i    = items.indexOf(item);
                            const qty  = parseFloat(item.qty  || 1);
                            const rate = parseFloat(item.rate || 0);
                            const amt  = qty * rate;
                            const nm   = (item.item_name || item.item_code || item.description || '').replace(/"/g,'&quot;');
                            return `<tr class="${qid}-row" data-idx="${i}" data-qty="${qty}" data-rate="${rate}" style="border-bottom:1px solid #f1f5f9;">
                                <td style="padding:2px 3px;">
                                    <input data-f="item_name" value="${nm}"
                                        style="width:100%;font-size:11px;border:1px solid #e2e8f0;border-radius:3px;padding:2px 4px;box-sizing:border-box;">
                                </td>
                                <td style="padding:2px 6px;font-size:11px;text-align:center;">${qty}</td>
                                <td style="padding:2px 6px;font-size:11px;text-align:right;">${rate.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                <td class="${qid}-amt" style="padding:2px 6px;font-size:11px;text-align:right;white-space:nowrap;">
                                    ${amt.toLocaleString('en-IN',{minimumFractionDigits:2})}
                                </td>
                            </tr>`;
                        }).join('');

                        const chargeRows = chargeItems.map((item) => {
                            const i   = items.indexOf(item);
                            const amt = parseFloat(item.qty||1) * parseFloat(item.rate||0);
                            const nm  = (item.item_name || item.item_code || item.description || '').replace(/</g,'&lt;');
                            return `<tr class="${qid}-charge-row" data-idx="${i}">
                                <td colspan="3" style="padding:3px 6px;font-size:11px;color:#94a3b8;font-style:italic;">${nm}</td>
                                <td style="padding:2px 3px;text-align:right;">
                                    <input class="${qid}-charge-amt" type="number" value="${amt.toFixed(2)}" min="0" step="0.01"
                                        style="width:80px;font-size:11px;border:1px solid #e2e8f0;border-radius:3px;padding:2px 3px;text-align:right;color:#64748b;">
                                </td>
                            </tr>`;
                        }).join('');

                        const rightPanel = `
                            <div style="background:#fff;border:1px solid #4f46e5;border-radius:8px;padding:10px;overflow:auto;max-height:520px;">
                                <div style="font-weight:600;color:#4f46e5;font-size:12px;margin-bottom:8px;border-bottom:1px solid #e2e8f0;padding-bottom:4px;">
                                    📄 Quotation
                                    <span style="font-size:10px;color:#94a3b8;font-weight:400;margin-left:6px;">Edit fields, then click Apply</span>
                                </div>
                                <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;">
                                    ${mkInp('customer','Customer', customer, true)}
                                    ${mkInp('txn_date','Date', txnDate)}
                                    ${mkInp('valid_till','Valid Till', validTill)}
                                </div>
                                <table style="width:100%;border-collapse:collapse;margin-bottom:8px;">
                                    <thead>
                                        <tr style="background:#f8fafc;border-bottom:2px solid #e2e8f0;">
                                            <th style="padding:4px 6px;font-size:11px;text-align:left;font-weight:600;color:#334155;">Item</th>
                                            <th style="padding:4px 6px;font-size:11px;text-align:center;font-weight:600;color:#334155;">Qty</th>
                                            <th style="padding:4px 6px;font-size:11px;text-align:right;font-weight:600;color:#334155;">Rate</th>
                                            <th style="padding:4px 6px;font-size:11px;text-align:right;font-weight:600;color:#334155;">Amt</th>
                                        </tr>
                                    </thead>
                                    <tbody>${editableItemRows}</tbody>
                                    <tfoot>
                                        <tr style="border-top:1px solid #e2e8f0;">
                                            <td colspan="3" style="padding:3px 6px;font-size:11px;color:#64748b;">Subtotal</td>
                                            <td id="${qid}-sub" style="padding:3px 6px;font-size:11px;text-align:right;">${subtotal.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                        </tr>
                                        ${chargeRows}
                                        ${taxAmt > 0.01 ? `<tr>
                                            <td colspan="3" style="padding:3px 6px;font-size:11px;color:#64748b;">Tax${taxRate ? ' ('+taxRate+'%)' : ''}</td>
                                            <td id="${qid}-tax" style="padding:3px 6px;font-size:11px;text-align:right;">${taxAmt.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                        </tr>` : ''}
                                        <tr style="border-top:2px solid #4f46e5;background:#f8fafc;">
                                            <td colspan="3" style="padding:5px 6px;font-size:12px;font-weight:700;color:#1e293b;">Grand Total (${currency})</td>
                                            <td id="${qid}-gt" style="padding:5px 6px;font-size:13px;font-weight:700;color:#4f46e5;text-align:right;">₹ ${grandTotal.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
                                        </tr>
                                    </tfoot>
                                </table>
                            </div>`;

                        const qCard = `
                            <div style="background:#fff;border-radius:8px;padding:4px 0;" id="${qid}">
                                <div style="display:flex;${is_mobile_view ? 'flex-direction:column;' : ''}gap:10px;align-items:flex-start;margin-bottom:8px;">
                                    ${leftPanel}
                                    <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;">
                                        ${rightPanel}
                                        <button id="${qid}-apply"
                                            style="display:none;width:100%;padding:7px;font-size:12px;font-weight:600;background:#e0e7ff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:6px;cursor:pointer;">
                                            🔄 Apply Changes
                                        </button>
                                    </div>
                                </div>
                                <button class="ai-create-quotation-btn"
                                    data-quotation='${JSON.stringify(q_data).replace(/'/g, "&#39;")}'
                                    style="width:100%;padding:10px;font-size:13px;font-weight:600;background:#4f46e5;color:#fff;border:none;border-radius:6px;cursor:pointer;">
                                    ✅ Create Quotation
                                </button>
                            </div>`;
                        if (el) el.innerHTML = qCard;

                        const qWrapper = document.getElementById(qid);

                        const qToggleBtn = qWrapper.querySelector(`#${qid}-toggle-left`);
                        if (qToggleBtn) {
                            let showingImage = true;
                            qToggleBtn.addEventListener('click', () => {
                                showingImage = !showingImage;
                                qWrapper.querySelector(`#${qid}-extracted`).style.display = showingImage ? 'none' : 'block';
                                qWrapper.querySelector(`#${qid}-imgview`).style.display   = showingImage ? 'block' : 'none';
                                qWrapper.querySelector(`#${qid}-left-title`).textContent  = showingImage ? (qIsPdf ? '📄 Uploaded PDF' : '📷 Uploaded Image') : '🔍 Extracted from Image';
                                qToggleBtn.textContent = showingImage ? '🔍 View Extracted' : (qIsPdf ? '📄 View PDF' : '📷 View Image');
                            });
                            let _qRot = 0;
                            const qInvImg = qWrapper.querySelector(`#${qid}-inv-img`);
                            const qRotL   = qWrapper.querySelector(`#${qid}-rot-l`);
                            const qRotR   = qWrapper.querySelector(`#${qid}-rot-r`);
                            if (qInvImg) {
                                if (qRotL) qRotL.addEventListener('click', () => { _qRot -= 90; qInvImg.style.transform = `rotate(${_qRot}deg)`; });
                                if (qRotR) qRotR.addEventListener('click', () => { _qRot += 90; qInvImg.style.transform = `rotate(${_qRot}deg)`; });
                                qInvImg.addEventListener('click', () => openAIImageLightbox(qImgSrc));
                            }
                        }

                        function recalcQuotationTotals() {
                            let newSub = 0;
                            qWrapper.querySelectorAll(`.${qid}-row`).forEach(tr => {
                                const q = parseFloat(tr.dataset.qty)  || 0;
                                const r = parseFloat(tr.dataset.rate) || 0;
                                const a = q * r;
                                tr.querySelector(`.${qid}-amt`).textContent = a.toLocaleString('en-IN',{minimumFractionDigits:2});
                                newSub += a;
                            });
                            let newCharges = 0;
                            qWrapper.querySelectorAll(`.${qid}-charge-amt`).forEach(inp => {
                                newCharges += parseFloat(inp.value) || 0;
                            });
                            const newTaxableBase = newSub + newCharges;
                            const newTax = Math.round(newTaxableBase * taxRate / 100 * 100) / 100;
                            qWrapper.querySelector(`#${qid}-sub`).textContent = newSub.toLocaleString('en-IN',{minimumFractionDigits:2});
                            const qTaxEl = qWrapper.querySelector(`#${qid}-tax`);
                            if (qTaxEl) qTaxEl.textContent = newTax.toLocaleString('en-IN',{minimumFractionDigits:2});
                            qWrapper.querySelector(`#${qid}-gt`).textContent = '₹ ' + (newTaxableBase + newTax).toLocaleString('en-IN',{minimumFractionDigits:2});
                        }
                        const qApplyBtn = qWrapper.querySelector(`#${qid}-apply`);
                        const qShowApply = () => { qApplyBtn.style.display = 'block'; };

                        qWrapper.querySelectorAll(`.${qid}-charge-amt`).forEach(i => {
                            i.addEventListener('input', () => { recalcQuotationTotals(); qShowApply(); });
                        });
                        qWrapper.querySelectorAll(`#${qid}-customer,#${qid}-txn_date,#${qid}-valid_till`).forEach(i => {
                            i.addEventListener('input', qShowApply);
                        });
                        qWrapper.querySelectorAll('[data-f="item_name"]').forEach(i => i.addEventListener('input', qShowApply));

                        qApplyBtn.addEventListener('click', function() {
                            const newMapped = Object.assign({}, q_data.mapped_data);
                            newMapped.customer_name = qWrapper.querySelector(`#${qid}-customer`).value;
                            newMapped.transaction_date = qWrapper.querySelector(`#${qid}-txn_date`).value;
                            newMapped.valid_till = qWrapper.querySelector(`#${qid}-valid_till`).value;

                            const newItems = [];
                            qWrapper.querySelectorAll(`.${qid}-row`).forEach(tr => {
                                const orig = (q_data.mapped_data.items || [])[parseInt(tr.dataset.idx)] || {};
                                newItems.push(Object.assign({}, orig, {
                                    item_name: tr.querySelector('[data-f="item_name"]').value,
                                    qty:  parseFloat(tr.dataset.qty)  || 1,
                                    rate: parseFloat(tr.dataset.rate) || 0,
                                }));
                            });
                            qWrapper.querySelectorAll(`.${qid}-charge-row`).forEach(tr => {
                                const orig = (q_data.mapped_data.items || [])[parseInt(tr.dataset.idx)] || {};
                                const editedAmt = parseFloat(tr.querySelector(`.${qid}-charge-amt`).value) || 0;
                                newItems.push(Object.assign({}, orig, { qty: 1, rate: editedAmt }));
                            });
                            newMapped.items = newItems;
                            const newRegSub  = newItems.filter(it => !_qIsCharge(it)).reduce((s, it) => s + it.qty * it.rate, 0);
                            const newChgSub  = newItems.filter(it => _qIsCharge(it)).reduce((s, it) => s + it.qty * it.rate, 0);
                            const newBase    = newRegSub + newChgSub;
                            newMapped.grand_total = Math.round((newBase + newBase * taxRate / 100) * 100) / 100;

                            const newQData = Object.assign({}, q_data, { mapped_data: newMapped });
                            qWrapper.querySelector('.ai-create-quotation-btn').setAttribute('data-quotation', JSON.stringify(newQData).replace(/'/g,"&#39;"));

                            this.textContent = '✅ Applied!';
                            setTimeout(() => { this.style.display = 'none'; this.textContent = '🔄 Apply Changes'; }, 1200);
                        });

                        // Bind create button
                        qWrapper.querySelector('.ai-create-quotation-btn').addEventListener('click', function () {
                            const data = this.getAttribute('data-quotation');
                            this.disabled = true;
                            this.textContent = 'Creating...';
                            const btn = this;
                            frappe.call({
                                method: 'pilot_assist.api.ai_api.create_quotation_from_data',
                                args: { data: data },
                                callback: function (res) {
                                    if (res.message) {
                                        const msg = res.message;

                                        if (msg.status === 'items_not_found') {
                                            btn.disabled = false;
                                            btn.textContent = 'Create Quotation';
                                            const itemList = msg.not_found.map(i => `<strong>${i}</strong>`).join(', ');
                                            const warnDiv = document.createElement('div');
                                            warnDiv.style.cssText = 'margin-top:8px;padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:12px;color:#856404;';
                                            warnDiv.innerHTML = `⚠️ Could not create Quotation.<br>The following could not be matched in ERPNext:<br>${itemList}<br><br>Please add them first (or correct the name/spelling), then try again.`;
                                            el.querySelector('div').appendChild(warnDiv);
                                            return;
                                        }

                                        const { name, url } = msg;
                                        btn.remove();
                                        const successDiv = document.createElement('div');
                                        successDiv.style.cssText = 'margin-top:8px;padding:8px;background:#d4f1c0;border-radius:6px;font-size:12px;';
                                        successDiv.innerHTML = `✅ Created: <strong>${name}</strong><br><a href="${url}" target="_blank" style="color:#4f46e5;">Click to open →</a>`;
                                        el.querySelector('div').appendChild(successDiv);

                                        active_doctype = 'Quotation';
                                        active_docname = name;
                                        conversation_history.push({
                                            role: 'model',
                                            content: `Quotation ${name} has been created successfully from the uploaded image. You can now ask questions about this document.`
                                        });

                                        setTimeout(() => {
                                            sendMessage(`Quotation ${name} was just created. Review the document and ask me 3 short follow-up questions about what I should verify or do next — like customer details, item pricing, validity date, or any missing fields. Keep it brief.`, true);
                                        }, 500);
                                    }
                                }
                            });
                        });
                    } catch(e) {
                        if (el) el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">${response_text}</div>`;
                    }
                } else if (response_text.startsWith("ROLE_SELECT:")) {
                    // User's account matches more than one configured role —
                    // show the options as buttons; picking one sets
                    // selected_ai_role (sticky for the rest of this panel
                    // session) and silently resends the same original
                    // question now that a role is attached.
                    try {
                        const role_data = JSON.parse(response_text.replace("ROLE_SELECT:", ""));
                        const roles = role_data.roles || [];
                        const roleButtons = roles.map(r =>
                            `<button class="ai-role-choice-btn btn btn-xs btn-default" data-role="${r}" style="margin:2px 4px 2px 0;">${r}</button>`
                        ).join('');
                        if (el) {
                            el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">
                                Your account matches more than one role. Please choose which one to use for this chat:
                                <div style="margin-top:6px;">${roleButtons}</div>
                            </div>`;
                            el.querySelectorAll('.ai-role-choice-btn').forEach(btn => {
                                btn.addEventListener('click', function () {
                                    selected_ai_role = this.getAttribute('data-role');
                                    localStorage.setItem(_role_storage_key, selected_ai_role);
                                    if (roleDropdownEl) roleDropdownEl.value = selected_ai_role;
                                    el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">Using role: <strong>${selected_ai_role}</strong></div>`;
                                    sendMessage(message, true);
                                });
                            });
                        }
                    } catch (e) {
                        if (el) el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">${response_text}</div>`;
                    }
                } else if (response_text.startsWith("LIST_DATA:")) {
                    try {
                        const list_data = JSON.parse(response_text.replace("LIST_DATA:", ""));
                        const rows    = Array.isArray(list_data.rows) ? list_data.rows : [];
                        // total is null/undefined whenever the backend can't yet prove that's
                        // the true full count (MCP's own "count" field only reflects how many
                        // rows a given limited fetch returned, not the true database total) —
                        // show "X shown so far" in that case instead of guessing a number.
                        const hasKnownTotal = (list_data.total !== null && list_data.total !== undefined);
                        let total = hasKnownTotal ? list_data.total : null;
                        const columns = (Array.isArray(list_data.fields) && list_data.fields.length)
                            ? list_data.fields
                            : (rows[0] ? Object.keys(rows[0]) : []);
                        const lid = 'lst' + Date.now();

                        const rowToHtml = row => `<tr>${columns.map(c => `<td style="padding:4px 8px;font-size:12px;border-bottom:1px solid #f1f5f9;">${row[c] !== undefined && row[c] !== null ? row[c] : ''}</td>`).join('')}</tr>`;

                        const tableHtml = `
                            <table style="width:100%;border-collapse:collapse;margin-top:6px;">
                                <thead><tr>${columns.map(c => `<th style="padding:4px 8px;font-size:11px;text-align:left;color:#64748b;border-bottom:1px solid #e2e8f0;">${c}</th>`).join('')}</tr></thead>
                                <tbody id="${lid}-body">${rows.map(rowToHtml).join('')}</tbody>
                            </table>`;

                        // We only ever reach this branch when rows.length > 10 (the backend's own
                        // trigger condition), so there's always more to load here — no need to
                        // compare against total, which may not even be known yet.
                        const btnLabel = (t) => `Load More (${t !== null ? rows.length + ' of ' + t : rows.length + ' shown so far'})`;
                        const showMoreBtn = `<button id="${lid}-more" style="margin-top:8px;width:100%;padding:6px;font-size:12px;background:#eef2ff;color:#4f46e5;border:1px solid #c7d2fe;border-radius:6px;cursor:pointer;">${btnLabel(total)}</button>`;

                        if (el) {
                            el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">
                                ${_ai_markdown_to_html(list_data.summary || '')}
                                <div style="overflow-x:auto;">${tableHtml}</div>
                                ${showMoreBtn}
                            </div>`;

                            let offset = rows.length;
                            const moreBtn = el.querySelector(`#${lid}-more`);
                            if (moreBtn) {
                                moreBtn.addEventListener('click', function () {
                                    const btn = this;
                                    btn.disabled = true;
                                    btn.textContent = 'Loading...';
                                    frappe.call({
                                        method: 'pilot_assist.api.ai_api.load_more_list_records',
                                        args: {
                                            doctype: list_data.doctype,
                                            filters: JSON.stringify(list_data.filters || {}),
                                            fields: JSON.stringify(list_data.fields || []),
                                            offset: offset,
                                            page_size: 10
                                        },
                                        callback: function (r) {
                                            const more = r.message || {};
                                            const newRows = Array.isArray(more.rows) ? more.rows : [];
                                            const tbody = el.querySelector(`#${lid}-body`);
                                            if (tbody) tbody.innerHTML += newRows.map(rowToHtml).join('');
                                            offset += newRows.length;
                                            if (more.has_more) {
                                                btn.disabled = false;
                                                const moreTotal = (more.total !== null && more.total !== undefined) ? more.total : null;
                                                btn.textContent = `Load More (${offset}${moreTotal !== null ? ' of ' + moreTotal : ' shown so far'})`;
                                            } else {
                                                btn.remove();
                                            }
                                        },
                                        error: function () {
                                            btn.disabled = false;
                                            btn.textContent = 'Load More (retry)';
                                            const prevErr = el.querySelector(`.${lid}-err`);
                                            if (prevErr) prevErr.remove();
                                            const doctypeSlug = (list_data.doctype || '').toLowerCase().replace(/ /g, '-');
                                            const errDiv = document.createElement('div');
                                            errDiv.className = `${lid}-err`;
                                            errDiv.style.cssText = 'margin-top:6px;padding:6px 10px;background:#fde8e8;color:#b91c1c;border-radius:6px;font-size:11px;';
                                            errDiv.innerHTML = `⚠️ Something went wrong loading more records. <a href="/app/${doctypeSlug}" target="_blank" style="color:#b91c1c;text-decoration:underline;">Check ${list_data.doctype} directly</a> or tap Load More to try again.`;
                                            btn.insertAdjacentElement('afterend', errDiv);
                                            setTimeout(() => errDiv.remove(), 8000);
                                        }
                                    });
                                });
                            }
                        }
                    } catch (e) {
                        if (el) el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">${response_text}</div>`;
                    }
                } else {
                    // When the answer is about exactly one document, the backend
                    // appends a plain Markdown link (e.g. "[Open SINV-00042 →](/app/...)")
                    // straight onto the response text — no change to the response
                    // shape at all, still always a plain string. _ai_markdown_to_html
                    // turns that Markdown link into a real clickable <a> automatically,
                    // same as it already does for tables and any other markdown.
                    let formatted = _ai_markdown_to_html(response_text);
                    if (el) el.innerHTML = `<div style="background:#f0f0f0;padding:6px 10px;border-radius:10px;">${formatted}</div>`;
                }

                history.scrollTop = history.scrollHeight;
                conversation_history.push({ role: "user", content: message });
                conversation_history.push({ role: "model", content: response_text });

                is_ai_busy = false;
                if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = 'Send'; }
            },
            error: function () {
                // Without this, a failed request leaves the "Thinking..." bubble stuck
                // forever — Frappe's own error popup appears, but nothing here ever
                // touches this specific message element to resolve it.
                let el = panel.querySelector(`[data-msg-id="${msg_id}"]`);
                if (el) {
                    el.innerHTML = `<div style="background:#fde8e8;color:#b91c1c;padding:6px 10px;border-radius:10px;">⚠️ Something went wrong. Please try again.</div>`;
                }
                history.scrollTop = history.scrollHeight;

                is_ai_busy = false;
                if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = 'Send'; }
            }
        });
    }

    // Send button click
    panel.querySelector('.ai-send-btn').addEventListener('click', function () {
        const msgInput = panel.querySelector('.ai-message-input');
        const message = msgInput.value.trim();
        if (!message) return;
        sendMessage(message);
    });

    // Enter key to send
    panel.querySelector('.ai-message-input').addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            const message = this.value.trim();
            if (!message) return;
            sendMessage(message);
        }
    });

    // Voice input (mic button) with auto-stop on 3s silence + auto-send.
    // Button itself is only rendered when Sarvam speech-to-text is
    // configured (see frappe.boot.enable_speech_to_text above) — skip all
    // of this setup entirely when it's absent, instead of throwing on a
    // null micBtn.
    const micBtn = panel.querySelector('.ai-mic-btn');
    const micTooltip = panel.querySelector('.ai-mic-tooltip');
    if (micBtn) {
    let mediaRecorder = null;
    let audioChunks = [];
    let isRecording = false;
    let voiceAudioContext = null;
    let silenceStart = null;
    let silenceCheckRAF = null;
    const msgInputEl = panel.querySelector('.ai-message-input');

    const SILENCE_THRESHOLD = 12;
    const SILENCE_DURATION_MS = 3000;

    function stopVoiceRecording() {
        if (mediaRecorder && isRecording) {
            mediaRecorder.stop();
        }
        isRecording = false;
        micBtn.innerHTML = '🎤';
        micBtn.style.background = '';
        micBtn.style.color = '';
        if (micTooltip) micTooltip.style.display = 'none';
        if (silenceCheckRAF) cancelAnimationFrame(silenceCheckRAF);
        if (voiceAudioContext) {
            voiceAudioContext.close();
            voiceAudioContext = null;
        }
    }

    micBtn.addEventListener('click', async function () {
        if (!isRecording) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];

                // Silence detection setup
                voiceAudioContext = new (window.AudioContext || window.webkitAudioContext)();
                const source = voiceAudioContext.createMediaStreamSource(stream);
                const analyser = voiceAudioContext.createAnalyser();
                analyser.fftSize = 512;
                source.connect(analyser);
                const dataArray = new Uint8Array(analyser.frequencyBinCount);
                silenceStart = null;

                function checkSilence() {
                    if (!isRecording) return;
                    analyser.getByteFrequencyData(dataArray);
                    const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;

                    if (avg < SILENCE_THRESHOLD) {
                        if (!silenceStart) silenceStart = Date.now();
                        else if (Date.now() - silenceStart > SILENCE_DURATION_MS) {
                            stopVoiceRecording();
                            return;
                        }
                    } else {
                        silenceStart = null;
                    }
                    silenceCheckRAF = requestAnimationFrame(checkSilence);
                }
                checkSilence();

                mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
                mediaRecorder.onstop = function () {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const reader = new FileReader();
                    reader.onloadend = function () {
                        const base64Audio = reader.result.split(',')[1];
                        msgInputEl.placeholder = 'Transcribing...';
                        msgInputEl.disabled = true;
                        frappe.call({
                            method: 'pilot_assist.api.ai_api.speech_to_text',
                            args: { audio_data: base64Audio },
                            callback: function (r) {
                                msgInputEl.placeholder = 'Type your message...';
                                msgInputEl.disabled = false;
                                if (r.message && r.message.trim()) {
                                    sendMessage(r.message.trim());
                                }
                            },
                            error: function () {
                                msgInputEl.placeholder = 'Type your message...';
                                msgInputEl.disabled = false;
                            }
                        });
                    };
                    reader.readAsDataURL(audioBlob);
                    stream.getTracks().forEach(track => track.stop());
                };

                mediaRecorder.start();
                isRecording = true;
                micBtn.innerHTML = '🔴';
                micBtn.style.background = '#ef4444';
                micBtn.style.color = 'white';
                if (micTooltip) micTooltip.style.display = 'block';
            } catch (err) {
                frappe.msgprint('Microphone access denied or not available.');
            }
        } else {
            stopVoiceRecording();
        }
    });
    }

    // Quick prompt buttons
    panel.querySelectorAll('.ai-suggestion-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            sendMessage(this.getAttribute('data-prompt'));
        });
    });

    // File upload
    const fileInput = panel.querySelector('.ai-file-input');
    const removeBtn = panel.querySelector('.ai-file-remove');

    fileInput.addEventListener('change', async function (e) {
        const file = e.target.files[0];
        const preview = panel.querySelector('.ai-file-preview');
        if (!file) { image_data = null; image_mime = null; preview.textContent = ''; removeBtn.style.display = 'none'; return; }

        preview.textContent = 'Processing...';
        try {
            if (file.type === 'application/pdf') {
                if (file.size > 10 * 1024 * 1024) { frappe.msgprint("PDF too large. Max 10MB."); e.target.value = ''; return; }
                image_mime = 'application/pdf';
                image_data = await new Promise((resolve) => {
                    let reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result.split(',')[1]);
                    reader.readAsDataURL(file);
                });
                preview.textContent = `PDF ready: ${file.name}`;
                removeBtn.style.display = 'inline-block';
            } else if (file.type.startsWith('image/')) {
                let blob = await resizeImage(file);
                image_mime = 'image/jpeg';
                image_data = await new Promise((resolve) => {
                    let reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result.split(',')[1]);
                    reader.readAsDataURL(blob);
                });
                preview.innerHTML = `
                    <img src="data:image/jpeg;base64,${image_data}" class="ai-img-thumb"
                        style="max-height:48px;border-radius:4px;cursor:pointer;border:1px solid #e2e8f0;vertical-align:middle;"
                        title="Click to view full size">
                    <span style="vertical-align:middle;margin-left:6px;font-size:11px;color:#555;">${file.name}</span>`;
                removeBtn.style.display = 'inline-block';
                preview.querySelector('.ai-img-thumb').addEventListener('click', () =>
                    openAIImageLightbox(`data:image/jpeg;base64,${image_data}`)
                );
            } else {
                frappe.msgprint("Only images and PDFs are supported.");
                e.target.value = '';
            }
        } catch (err) {
            console.error("File error:", err);
            preview.textContent = 'Error processing file.';
        }
    });

    removeBtn.addEventListener('click', function () {
        image_data = null;
        image_mime = null;
        fileInput.value = '';
        panel.querySelector('.ai-file-preview').textContent = '';
        removeBtn.style.display = 'none';
    });
}

function openAIImageLightbox(src) {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.88);z-index:99999;display:flex;flex-direction:column;align-items:center;justify-content:center;';
    overlay.innerHTML = `
        <div style="display:flex;gap:10px;margin-bottom:12px;align-items:center;">
            <button data-act="rotl" title="Rotate Left"
                style="padding:6px 16px;font-size:18px;background:rgba(255,255,255,0.15);color:#fff;border:1px solid rgba(255,255,255,0.3);border-radius:6px;cursor:pointer;">↺</button>
            <button data-act="rotr" title="Rotate Right"
                style="padding:6px 16px;font-size:18px;background:rgba(255,255,255,0.15);color:#fff;border:1px solid rgba(255,255,255,0.3);border-radius:6px;cursor:pointer;">↻</button>
            <button data-act="close" title="Close"
                style="padding:6px 16px;font-size:18px;background:rgba(255,255,255,0.15);color:#fff;border:1px solid rgba(255,255,255,0.3);border-radius:6px;cursor:pointer;">✕</button>
        </div>
        <div style="overflow:auto;max-width:92vw;max-height:84vh;display:flex;align-items:center;justify-content:center;">
            <img src="${src}" style="max-width:88vw;max-height:80vh;border-radius:8px;object-fit:contain;display:block;box-shadow:0 8px 32px rgba(0,0,0,0.5);transition:transform 0.25s ease;transform-origin:center center;">
        </div>`;
    document.body.appendChild(overlay);

    let _rot = 0;
    const img = overlay.querySelector('img');
    const close = () => { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); };

    overlay.querySelectorAll('button').forEach(btn => {
        btn.addEventListener('click', e => {
            e.stopPropagation();
            const act = btn.getAttribute('data-act');
            if (act === 'rotl') { _rot -= 90; img.style.transform = `rotate(${_rot}deg)`; }
            if (act === 'rotr') { _rot += 90; img.style.transform = `rotate(${_rot}deg)`; }
            if (act === 'close') close();
        });
    });
    overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
    const esc = e => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } };
    document.addEventListener('keydown', esc);
}

async function resizeImage(file) {
    return new Promise((resolve) => {
        let img = new Image();
        let url = URL.createObjectURL(file);
        img.onload = function () {
            let w = img.width, h = img.height;
            let maxSize = 1024;
            if (w > maxSize || h > maxSize) {
                let ratio = Math.min(maxSize / w, maxSize / h);
                w = Math.floor(w * ratio);
                h = Math.floor(h * ratio);
            }
            let canvas = document.createElement('canvas');
            canvas.width = w; canvas.height = h;
            canvas.getContext('2d').drawImage(img, 0, 0, w, h);
            URL.revokeObjectURL(url);
            canvas.toBlob(resolve, 'image/jpeg', 0.8);
        };
        img.src = url;
    });
}
})();
