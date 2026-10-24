import frappe
from frappe import _


_FUZZY_MATCH_THRESHOLD = 0.80


_GEMINI_MINUTE_COOLDOWN_SEC = 60
_GEMINI_DAILY_COOLDOWN_SEC = 6 * 60 * 60


def _ai_conf(key, default=None):
    """Read an AI-assistant config key from the nested site_config.json
    block:
        "enable_ai_assistant": {
            "enable_ai_chat": 1,
            "gemini_api_keys": ["...", "..."],
            "sarvam_api_key": "...",
            "enable_speech_to_text": 1,
            "enable_role_based_ai": 1,
            "role_based_ai_credentials": [...]
        }
    All AI-related site config now lives under this one object instead of
    as flat top-level keys, so every reader goes through here."""
    return (frappe.conf.get("enable_ai_assistant") or {}).get(key, default)


def _mcp_url():
    """Full URL for the frappe_assistant_core MCP endpoint. Defaults to THIS
    site's own URL, since frappe_assistant_core is installed alongside
    pilot_assist on each client site — the endpoint path itself
    (/api/method/frappe_assistant_core...) is identical everywhere, so this
    makes every new client deployment work with zero code changes. A site
    can still override the base URL via enable_ai_assistant.mcp_base_url in
    site_config.json for cases where MCP is served elsewhere (e.g. local dev
    without frappe_assistant_core installed, pointed at a real server)."""
    base = _ai_conf("mcp_base_url") or frappe.utils.get_url()
    return f"{base.rstrip('/')}/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp"


def _check_assistant_access(current_user):
    """Returns (allowed, message). Distinguishes a user's own access flag
    being off from the "assistant_enabled" field not existing at all on
    this site — the latter happens when this same code is deployed to a
    client site that hasn't added the custom field to User yet, and would
    otherwise surface as a raw SQL error instead of a clear message."""
    try:
        enabled = frappe.db.get_value("User", current_user, "assistant_enabled")
    except Exception:
        return False, _("The 'Enable Assistant Access' field is not set up on this site yet. Please contact admin.")
    if not enabled:
        return False, _("You don't have access to this. Please contact admin.")
    return True, None


def _matched_role_entries(current_user):
    """Entries in role_based_ai_credentials whose role this user actually
    has assigned. Empty for Administrator (always uses its own key — see
    _get_role_based_mcp_token) or when role-based mode is off."""
    if current_user == "Administrator":
        return []
    if not _ai_conf("enable_role_based_ai"):
        return []
    credentials = _ai_conf("role_based_ai_credentials") or []
    user_roles = frappe.get_roles(current_user)
    return [
        entry for entry in credentials
        if entry.get("role") in user_roles and entry.get("token")
    ]


def _get_matched_role_choices(current_user):
    """Role names (not tokens) this user matches — exposed via boot_session
    so the chat widget can show a role-picker dropdown in its header
    immediately on open, instead of only discovering the options after the
    user's first message hits the in-chat ROLE_SELECT prompt. Empty unless
    there are 2+ matches — 0 or 1 match needs no picker at all. Also empty
    if the user's own "Enable Assistant Access" checkbox is off, so the
    dropdown doesn't misleadingly offer a choice to someone who's actually
    blocked from using the assistant at all."""
    allowed, _msg = _check_assistant_access(current_user)
    if not allowed:
        return []
    matched = _matched_role_entries(current_user)
    return [entry.get("role") for entry in matched] if len(matched) > 1 else []


def boot_session(bootinfo):
    bootinfo.enable_ai_chat = _ai_conf("enable_ai_chat", 0)
    bootinfo.enable_speech_to_text = _ai_conf("enable_speech_to_text", 0)
    bootinfo.ai_role_choices = _get_matched_role_choices(frappe.session.user)


# ─── Role-based assistance mode ────────────────────────────────────────────────
# Controlled by two site config keys:
#   enable_role_based_ai      — on/off switch
#   role_based_ai_credentials — array of {"role": "<Frappe Role>", "token": "<api_key>:<api_secret>"}
#       e.g. [{"role": "Sales User", "token": "789c287fd5b3efd:d283feb0aeb4296"}, ...]
#
# When on: the logged-in user's own Frappe Role is checked against this array.
#   - No matching role  → the request is refused outright; no AI/MCP call is
#     made at all, the chat just shows an access-denied message.
#   - Matching role     → MCP is called using THAT entry's token, not the
#     logged-in person's own api_key/api_secret — so access is controlled by
#     which token is configured for each role, not by what that specific
#     person's own account happens to have permission to do.
def _get_role_based_mcp_token(current_user, selected_role=None, allow_role_prompt=False):
    """Return (role_based_mode_on, token, role_choices).

    role_based_mode_on=False → mode is off, OR this user matched more than
    one configured role and allow_role_prompt=False (see below) — caller
    should use today's (unrestricted, per-user credential) behavior.
    role_based_mode_on=True, token=None, role_choices=None → mode is on but
    this user's role matched nothing configured — caller should refuse.
    role_based_mode_on=True, token=None, role_choices=[...] → this user
    matched 2+ roles and allow_role_prompt=True, but no valid selected_role
    was given yet — caller should ask the user to pick one of role_choices.
    role_based_mode_on=True, token=<str>, role_choices=None → use this
    token for MCP calls (either exactly one role matched, or selected_role
    resolved one of the 2+ matches).

    allow_role_prompt=False (the default) preserves the original behavior
    for callers with no UI to prompt from (e.g. chat_api): a 2+-role match
    falls back straight to the user's own real credentials, same as
    role-based mode being off. Only a caller that passes
    allow_role_prompt=True (the native chat widget) gets the role_choices
    signal instead, so it can render a picker and re-call with the chosen
    selected_role.

    The literal Administrator account always uses its own real credentials,
    regardless of role-based mode or allow_role_prompt — Frappe treats
    Administrator as implicitly holding every Role that exists, so without
    this it would always match whatever role happens to be configured,
    silently substituting a different, more restricted account's token.
    """
    if current_user == "Administrator":
        return False, None, None

    if not _ai_conf("enable_role_based_ai"):
        return False, None, None

    matched = _matched_role_entries(current_user)

    if len(matched) == 0:
        return True, None, None
    if len(matched) == 1:
        return True, matched[0].get("token"), None

    # 2+ matches.
    if not allow_role_prompt:
        return False, None, None

    if selected_role:
        for entry in matched:
            if entry.get("role") == selected_role:
                return True, entry.get("token"), None

    # No (valid) selection yet — signal the caller to prompt for one.
    return True, None, [entry.get("role") for entry in matched]


# ─── Shared tool definitions (provider-agnostic) ──────────────────────────────
_TOOL_SPECS = [
    {
        "name": "list_documents",
        "description": (
            "List records from ERPNext database. "
            "ALWAYS pass 'fields' — without it only name/creation/modified are returned. "
            "For top-N or ranking queries fetch with fields+limit=1000, then rank/sum yourself. "
            "NEVER call get_document in a loop to enrich records — use fields instead. "
            "For name filters use LIKE: {'customer_name': ['like', '%Shreya%']}. "
            "For dates: {'posting_date': ['>=', '2026-06-01']}. Never use 'between'."
        ),
        "params": {
            "doctype": ("string", "ERPNext DocType e.g. Sales Invoice, Purchase Invoice, Sales Order, Payment Entry"),
            "filters": ("object", "Filter dict. Use LIKE for names, >= for dates."),
            "fields":  ("array",  "Fieldnames to return e.g. ['party_name','paid_amount','payment_type','posting_date','grand_total','customer']. Always specify this."),
            "limit":   ("integer", "Max records to return. Use 1000 for aggregation/ranking."),
        },
        "required": ["doctype"]
    },
    {
        "name": "get_document",
        "description": "Get a specific ERPNext document by ID. Use when user asks about one specific record.",
        "params": {
            "doctype": ("string", "DocType name"),
            "name":    ("string", "Document ID"),
        },
        "required": ["doctype", "name"]
    },
    {
        "name": "search_documents",
        "description": "Free-text search across ERPNext. Use only when user doesn't specify a DocType.",
        "params": {
            "query": ("string", "Search query"),
            "limit": ("integer", "Max results"),
        },
        "required": ["query"]
    },
    {
        "name": "generate_report",
        "description": (
            "Run an ERPNext saved report. Use for total/summary/analytics queries. "
            "Known working reports: 'Sales Register', 'Purchase Register', "
            "'Accounts Receivable', 'Accounts Payable', 'Stock Balance', "
            "'General Ledger', 'Trial Balance', 'Profit and Loss Statement'. "
            "If unsure of the exact name, use list_documents instead."
        ),
        "params": {
            "report_name": ("string", "Exact ERPNext report name from the list above"),
            "filters":     ("object", "Report filters e.g. {'from_date': '2026-06-01', 'to_date': '2026-06-30'}"),
        },
        "required": ["report_name"]
    },
    {
        "name": "get_doctype_info",
        "description": "Get field structure of an ERPNext DocType. Call before creating a document to know exact field names.",
        "params": {
            "doctype": ("string", "DocType name e.g. Purchase Invoice"),
        },
        "required": ["doctype"]
    },
    {
        "name": "preview_purchase_invoice",
        "description": (
            "Use when user uploads an image AND wants to create a Purchase Invoice. "
            "Follow these steps strictly:\n"
            "RULES — apply to every field below, no exceptions:\n"
            "1. LABEL-DRIVEN, NOT POSITION-DRIVEN. For every charge, discount, or total row, read the "
            "printed label text next to the value. Never assume a row's identity from its position in the "
            "table — the row after an item is NOT automatically Freight; check what it is actually labeled "
            "(e.g. a row labeled 'Insurance' stays Insurance, never Freight).\n"
            "2. NEVER CALCULATE OR GUESS A MISSING VALUE. If a rate, subtotal, tax amount, or grand total "
            "is not explicitly printed on the document, omit that key entirely. Do NOT compute it by "
            "summing, multiplying, or dividing other fields, even if you are confident the math would be "
            "correct — a missing printed value stays missing, never calculated.\n"
            "3. HANDWRITTEN CORRECTIONS ALWAYS OVERRIDE PRINTED VALUES. If a printed figure has been struck "
            "through, corrected, or has a handwritten value written near it (very common for cash amounts), "
            "extract the handwritten replacement, not the original printed figure.\n"
            "4. DOCUMENT NUMBER DISCIPLINE. Invoice No / Bill No is ONLY the number beside a label reading "
            "'Invoice No', 'Invoice Number', 'Tax Invoice No', or 'Bill No'. Never substitute Challan No, "
            "DC No, GRN No, PO No, e-Way Bill No, or Ack No for it, even if one of those is bigger, bolder, "
            "or closer to the total — each belongs only in its own field.\n"
            "5. DO NOT SKIP FIELDS BECAUSE THEY SEEM MINOR. Extract vehicle number, transporter name, LR "
            "number, payment terms, shipment number, e-way bill number, and supplier mobile/email whenever "
            "they appear anywhere on the document, including small print, headers, or footers.\n"
            "6. WHEN IN DOUBT, SAY SO. If a field is illegible, ambiguous, or inferred rather than read "
            "directly, still give your best read but mention it briefly in summary. Never silently drop a "
            "field without a reason.\n"
            "STEP 1 — Call get_doctype_info('Purchase Invoice') to get all available field names.\n"
            "STEP 2 — Extract EVERY piece of data visible in the invoice image. You MUST extract all of:\n"
            "(a) Invoice header: invoice number → bill_no (Rule 4 applies), invoice date → bill_date, "
            "IRN → irn, Ack No → ack_no, Ack Date → ack_date, place of supply → place_of_supply.\n"
            "(b) Supplier: name → supplier, GSTIN → supplier_gstin, "
            "full address text → address_display (do NOT use the supplier_address Link field), "
            "mobile/phone → supplier_mobile, email → supplier_email.\n"
            "(c) Buyer/Bill To — this is OUR OWN company receiving the goods, not the supplier: "
            "name → company, GSTIN → billing_gstin, "
            "full address text → billing_address_display (do NOT use the billing_address Link field).\n"
            "(d) Ship To: name, GSTIN, full address text → shipping_address_display, "
            "shipment/tracking number → shipment_no.\n"
            "(e) Transport: mode → lr_no, vehicle number → vehicle_no, "
            "transporter name → transporter_name, e-way bill → ewaybill.\n"
            "(f) Items table: for EACH row extract — "
            "item description → item_name, HSN/SAC code → gst_hsn_code, "
            "qty, UOM → uom, rate (Rule 2 applies — only if printed), net rate, total amount, "
            "per-row discount amount if a Discount column/value is printed for that row → "
            "discount_amount (Rule 2 applies — only if printed, never derived from rate/total).\n"
            "(g) Charge rows: packing/freight/transport/insurance/forwarding/other charges as separate "
            "items with qty=1 and the charge amount as rate, include their HSN code → gst_hsn_code "
            "(Rule 1 applies — use each row's own printed label).\n"
            "(h) Taxes: look BOTH in a dedicated HSN-wise tax table AND in the totals/summary block — "
            "many invoices print each tax as its own labeled line there instead of a table "
            "(e.g. 'Output Tax SGST ₹450.00', 'CGST ₹450.00', or a single 'IGST' line). Extract EVERY "
            "such labeled tax line you find as its own row in the 'taxes' array with description (the "
            "exact label, e.g. 'SGST', 'CGST', 'IGST'), HSN (if shown), taxable value, rate, and amount "
            "(Rule 2 applies — only if printed). Never merge multiple tax lines into a single "
            "total_taxes_and_charges figure — each labeled tax must be its own row so it maps to the "
            "correct tax account.\n"
            "(i) Totals: total before tax → base_net_total, total tax → total_taxes_and_charges, "
            "round off → rounding_adjustment, final amount → grand_total (Rule 2 applies to all four).\n"
            "(j) Bank: bank name, account number → bank_account, IFSC.\n"
            "(k) Terms: extract full terms and conditions text → terms, payment terms label/text (e.g. '30 days', 'Net 30') → payment_terms, actual printed due date value → due_date.\n"
            "STEP 3 — CRITICAL, do not skip: for ANY other labeled value visible on the document that "
            "isn't covered above — including site-specific custom fields (fieldnames starting with "
            "'custom_'), reference numbers, percentages, or codes — you MUST still map it using the "
            "full field list from get_doctype_info. Match by label/meaning even though this prompt "
            "doesn't name that field explicitly; never silently drop a visible value just because it "
            "wasn't enumerated above. Do NOT set Link fields (supplier_address, billing_address, "
            "shipping_address) from image text — use the _display variants. Do NOT invent a fieldname "
            "that get_doctype_info did not return.\n"
            "STEP 4 — Pass complete mapped_data as a JSON string with ALL extracted fields."
        ),
        "params": {
            "mapped_data": ("string", "JSON string of ALL mapped Purchase Invoice fields extracted from image"),
            "summary":     ("string", "Human readable summary of what was extracted"),
        },
        "required": ["mapped_data", "summary"]
    },
    {
        "name": "preview_quotation",
        "description": (
            "Use when user uploads an image AND wants to create a Quotation. "
            "Follow these steps strictly:\n"
            "RULES — apply to every field below, no exceptions:\n"
            "1. LABEL-DRIVEN, NOT POSITION-DRIVEN. Read the printed label text next to each value — "
            "never assume a row's identity from its position in the table.\n"
            "2. NEVER CALCULATE OR GUESS A MISSING VALUE. If a rate, subtotal, tax amount, or grand "
            "total is not explicitly printed on the document, omit that key entirely. Do NOT compute "
            "it by summing, multiplying, or dividing other fields, even if you are confident the math "
            "would be correct — a missing printed value stays missing, never calculated.\n"
            "3. HANDWRITTEN CORRECTIONS ALWAYS OVERRIDE PRINTED VALUES. If a printed figure has been "
            "struck through, corrected, or has a handwritten value written near it, extract the "
            "handwritten replacement, not the original printed figure.\n"
            "4. WHEN IN DOUBT, SAY SO. If a field is illegible, ambiguous, or inferred rather than read "
            "directly, still give your best read but mention it briefly in summary. Never silently "
            "drop a field without a reason.\n"
            "STEP 1 — Call get_doctype_info('Quotation') to get all available field names.\n"
            "STEP 2 — Extract EVERY piece of data visible in the quotation image. You MUST extract:\n"
            "(a) Header: quotation/reference number, quotation date → transaction_date, "
            "valid-until date → valid_till. Also look for OUR OWN issuing company's name — usually "
            "labeled 'Company Address Name' or printed near our own address/letterhead block, "
            "distinct from the Customer — and map it → company.\n"
            "(b) Customer: name → customer_name (also try party_name), "
            "full address text → address_display (do NOT use a Link field for this).\n"
            "(c) Items table: for EACH row extract — item description → item_name, "
            "qty, UOM → uom, rate (Rule 2 applies — only if printed), per-row amount/total.\n"
            "(d) Taxes: look BOTH in a dedicated tax table AND in the totals/summary block — "
            "many quotations print each tax as its own labeled line there instead of a table "
            "(e.g. 'Output Tax SGST ₹450.00', 'Output Tax CGST ₹450.00', or a single 'IGST' line). "
            "Extract EVERY such labeled tax line you find as its own row in the 'taxes' array with "
            "description (the exact label, e.g. 'SGST', 'CGST', 'IGST') and amount (Rule 2 applies — "
            "only if printed; a printed rate like '9.0%' next to it goes in that row's rate). Never "
            "merge multiple tax lines into a single total_taxes_and_charges figure — each labeled "
            "tax must be its own row so it maps to the correct tax account.\n"
            "(e) Totals: total before tax → total, total tax → total_taxes_and_charges, "
            "discount → discount_amount, final amount → grand_total (Rule 2 applies to all).\n"
            "(f) Terms: extract full terms and conditions text → terms.\n"
            "STEP 3 — CRITICAL, do not skip: for ANY other labeled value visible on the document that "
            "isn't covered above — including site-specific custom fields (fieldnames starting with "
            "'custom_'), reference numbers, percentages, or codes — you MUST still map it using the "
            "full field list from get_doctype_info. Match by label/meaning even though this prompt "
            "doesn't name that field explicitly; never silently drop a visible value just because it "
            "wasn't enumerated above. Do NOT invent a fieldname that get_doctype_info did not return.\n"
            "STEP 4 — Pass complete mapped_data as a JSON string with ALL extracted fields."
        ),
        "params": {
            "mapped_data": ("string", "JSON string of ALL mapped Quotation fields extracted from image"),
            "summary":     ("string", "Human readable summary of what was extracted"),
        },
        "required": ["mapped_data", "summary"]
    },
]


def _to_json_schema(spec):
    props = {}
    for k, (typ, desc) in spec["params"].items():
        if typ == "array":
            props[k] = {"type": "array", "items": {"type": "string"}, "description": desc}
        else:
            props[k] = {"type": typ, "description": desc}
    return {"type": "object", "properties": props, "required": spec.get("required", [])}


_PREVIEW_TOOL_NAMES = {"preview_purchase_invoice", "preview_quotation"}


def _gemini_tools(include_preview=False):
    from google.genai import types
    type_map = {"string": types.Type.STRING, "integer": types.Type.INTEGER, "object": types.Type.OBJECT, "array": types.Type.ARRAY}
    declarations = []
    for spec in _TOOL_SPECS:
        if spec["name"] in _PREVIEW_TOOL_NAMES and not include_preview:
            continue
        props = {}
        for k, (typ, desc) in spec["params"].items():
            if typ == "array":
                props[k] = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING), description=desc)
            else:
                props[k] = types.Schema(type=type_map.get(typ, types.Type.STRING), description=desc)
        declarations.append(types.FunctionDeclaration(
            name=spec["name"],
            description=spec["description"],
            parameters=types.Schema(type=types.Type.OBJECT, properties=props, required=spec.get("required", []))
        ))
    return [types.Tool(function_declarations=declarations)]


def _claude_tools():
    return [{"name": s["name"], "description": s["description"], "input_schema": _to_json_schema(s)} for s in _TOOL_SPECS]


def _oai_tools():
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": _to_json_schema(s)}} for s in _TOOL_SPECS]


def _bedrock_tools():
    return [{"toolSpec": {"name": s["name"], "description": s["description"], "inputSchema": {"json": _to_json_schema(s)}}} for s in _TOOL_SPECS]


def _validate_mapped_data(raw):
    """Parse + structurally validate the AI's mapped_data output for Purchase Invoice
    extraction. Works for ANY invoice — no invoice-specific values, just shape checks.
    Returns (mapped_dict, error_message). error_message is None when the shape looks
    usable; mapped_dict is only {} when nothing could be parsed at all, so the caller
    always gets a dict back instead of silently vanishing on malformed/truncated output."""
    import json as _json

    if isinstance(raw, dict):
        mapped = raw
    else:
        try:
            mapped = _json.loads(raw or "{}")
        except Exception as e:
            return {}, f"AI returned malformed JSON for the invoice data: {e}"

    if not isinstance(mapped, dict):
        return {}, "AI returned mapped_data that wasn't a JSON object."

    error = None
    items = mapped.get("items")
    if isinstance(items, str):
        try:
            items = _json.loads(items)
        except Exception:
            items = None
    if not isinstance(items, list) or len(items) == 0:
        error = "No items were extracted from the invoice — please review before creating the invoice."
    else:
        for idx, row in enumerate(items):
            if not isinstance(row, dict) or not (row.get("item_name") or row.get("item_code") or row.get("description")):
                error = f"Item row {idx + 1} is missing a name/description — please review before creating the invoice."
                break

    if not mapped.get("supplier"):
        error = error or "No supplier name was extracted — please review before creating the invoice."

    return mapped, error


def _validate_quotation_data(raw):
    """Parse + structurally validate the AI's mapped_data output for Quotation
    extraction. Works for ANY quotation — no quotation-specific values, just shape
    checks. Same contract as _validate_mapped_data: mapped_dict is only {} when
    nothing could be parsed at all, so the caller always gets a dict back."""
    import json as _json

    if isinstance(raw, dict):
        mapped = raw
    else:
        try:
            mapped = _json.loads(raw or "{}")
        except Exception as e:
            return {}, f"AI returned malformed JSON for the quotation data: {e}"

    if not isinstance(mapped, dict):
        return {}, "AI returned mapped_data that wasn't a JSON object."

    error = None
    items = mapped.get("items")
    if isinstance(items, str):
        try:
            items = _json.loads(items)
        except Exception:
            items = None
    if not isinstance(items, list) or len(items) == 0:
        error = "No items were extracted from the quotation — please review before creating it."
    else:
        for idx, row in enumerate(items):
            if not isinstance(row, dict) or not (row.get("item_name") or row.get("item_code") or row.get("description")):
                error = f"Item row {idx + 1} is missing a name/description — please review before creating it."
                break

    if not (mapped.get("customer_name") or mapped.get("party_name")):
        error = error or "No customer name was extracted — please review before creating the quotation."

    return mapped, error


# ─── Provider implementations ─────────────────────────────────────────────────

# DocTypes we know how to build a desk-form link for, and the field ERPNext
# stores their document name under in list_documents/get_document results —
# always "name", but kept as a set so this can be extended deliberately
# rather than linking every doctype the AI happens to be able to query.
_LINKABLE_DOCTYPES = {
    "Sales Invoice", "Purchase Invoice", "Sales Order", "Purchase Order",
    "Quotation", "Payment Entry", "Delivery Note", "Purchase Receipt",
}


def _extract_doc_names(doctype, mcp_data):
    """Return every document 'name' found in an mcp_data result for a
    linkable doctype — works for both get_document's single object and
    list_documents' {"data": [...]}. Empty list if nothing usable."""
    import json
    if doctype not in _LINKABLE_DOCTYPES:
        return []
    try:
        parsed = json.loads(mcp_data) if isinstance(mcp_data, str) else mcp_data
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    result = parsed.get("result", parsed)
    if not isinstance(result, dict):
        return []

    # get_document returns a single object (no "data" list, no "count").
    if "data" not in result and result.get("name"):
        return [result["name"]]

    # list_documents returns {"data": [...], "count": N}.
    data = result.get("data")
    if isinstance(data, list):
        return [row["name"] for row in data if isinstance(row, dict) and row.get("name")]

    return []


def _extract_single_doc_link(doctype, mcp_data):
    """If mcp_data represents exactly ONE record of a linkable doctype,
    return {"doctype": ..., "name": ...} for the frontend to render as a
    link. Returns None for 0 or >1 records, or an unlinkable doctype —
    the frontend then renders plain text with no link, same as today."""
    names = _extract_doc_names(doctype, mcp_data)
    if len(names) == 1:
        return {"doctype": doctype, "name": names[0]}
    return None


def _extract_list_rows(mcp_data):
    """Full row data (every requested field) + total count from a
    list_documents MCP result. Unlike _extract_doc_names (names only, for
    building a doc link), this keeps the actual field values so a table can
    be rebuilt and paginated ("Load More") on the frontend without calling
    Gemini again. Returns ([], 0) if mcp_data isn't a usable list result."""
    import json
    try:
        parsed = json.loads(mcp_data) if isinstance(mcp_data, str) else mcp_data
    except Exception:
        return [], 0
    if not isinstance(parsed, dict):
        return [], 0
    result = parsed.get("result", parsed)
    if not isinstance(result, dict):
        return [], 0
    data = result.get("data")
    rows = data if isinstance(data, list) else []
    total = result.get("count", len(rows))
    return rows, total


# ─── Gemini multi-key rotation on rate-limit ──────────────────────────────────

def _gemini_key_list():
    """Configured Gemini keys as a list."""
    keys = _ai_conf("gemini_api_keys")
    return [k for k in keys if k] if isinstance(keys, list) else []


def _gemini_cooldown_cache_key(api_key):
    # Last 8 chars only — enough to distinguish keys without storing full
    # secrets in cache key names (which can show up in logs/monitoring).
    return f"gemini_key_cooldown:{api_key[-8:]}"


def _gemini_pick_available_key(candidate_keys):
    """First key not currently resting from a prior rate-limit hit — checked
    fresh against Redis every call, so it sees cooldowns set by ANY worker
    process, not just this one. None if every candidate is still resting."""
    for k in candidate_keys:
        if not frappe.cache().get_value(_gemini_cooldown_cache_key(k)):
            return k
    return None


def _gemini_mark_rate_limited(api_key, error):
    """Rest this key for a length of time based on WHAT Google's error says,
    not a guess: a per-day quota failure rests for hours (a short retry is
    guaranteed to fail again); a per-minute limit rests only for Google's own
    stated retryDelay (falling back to _GEMINI_MINUTE_COOLDOWN_SEC if that
    detail isn't present in the error)."""
    import re as _re

    error_str = str(error)
    if "PerDay" in error_str:
        cooldown_sec = _GEMINI_DAILY_COOLDOWN_SEC
    else:
        match = _re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+)s", error_str)
        cooldown_sec = int(match.group(1)) if match else _GEMINI_MINUTE_COOLDOWN_SEC

    frappe.cache().set_value(_gemini_cooldown_cache_key(api_key), 1, expires_in_sec=cooldown_sec)


def _gemini_generate_with_rotation(keys, model, contents, config):
    """Try each configured key in turn, skipping ones currently resting.
    Only rotates on a 429/RESOURCE_EXHAUSTED response — any other error
    (bad key, network issue, etc.) is raised immediately, since switching
    keys wouldn't fix those."""
    from google import genai
    from google.genai import errors as genai_errors

    # Single key — no pool to switch within, so skip the cooldown check/mark
    # machinery entirely and call directly, exactly like the original
    # single-key behavior. A Redis lookup here would only add latency for
    # zero benefit (there's no alternative key to fall back to anyway), and
    # could even block a call that would've actually succeeded if a prior
    # cooldown estimate turns out to have been overly conservative.
    if len(keys) == 1:
        client = genai.Client(api_key=keys[0])
        return client.models.generate_content(model=model, contents=contents, config=config)

    tried = set()
    while len(tried) < len(keys):
        api_key = _gemini_pick_available_key([k for k in keys if k not in tried])
        if not api_key:
            break
        tried.add(api_key)
        try:
            client = genai.Client(api_key=api_key)
            return client.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.ClientError as e:
            is_rate_limited = getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e)
            if is_rate_limited:
                _gemini_mark_rate_limited(api_key, e)
                continue
            raise

    frappe.throw(_("All configured Gemini API keys are currently rate-limited. Please try again shortly."))


def _call_gemini(message, system_prompt, history_list, call_mcp, image_data=None, image_mime=None, enable_list_pagination=False):
    import base64
    import json
    from google.genai import types

    gemini_keys = _gemini_key_list()
    if not gemini_keys:
        frappe.throw(_("gemini_api_keys not configured in site_config.json"))

    model = _ai_conf("gemini_model") or "gemini-flash-lite-latest"
    is_extraction = bool(image_data)  # image present -> likely the invoice-extraction turn
    tools = _gemini_tools(include_preview=is_extraction)

    # Build contents with history
    contents = []
    for h in history_list:
        contents.append(types.Content(role=h.get("role", "user"), parts=[types.Part.from_text(text=h.get("content", ""))]))

    # Current user message (image first if present)
    parts = [types.Part.from_text(text=f"{system_prompt}\n\nUser: {message}")]
    if image_data and image_mime:
        parts.insert(0, types.Part.from_bytes(data=base64.b64decode(image_data), mime_type=image_mime))
    contents.append(types.Content(role="user", parts=parts))

    # Tracks the single-record link (if any) from the MOST RECENT tool call
    # this turn — used to attach a doc link to the final answer only when
    # that final answer is actually about one specific document.
    last_single_doc_link = None

    # Every document name seen from ANY tool call this turn, across all
    # rounds (e.g. all 5 names from a "list invoices this month" call) —
    # used as a fallback when Gemini answers a follow-up like "which one
    # has the highest total?" purely by reasoning over already-fetched
    # data, without making a fresh tool call in the same round.
    known_doc_names = {}  # name -> doctype

    # The most recent "browsable list" call this turn — list_documents with a
    # "listing" limit (<=50, the convention for a plain list, as opposed to
    # the limit=1000 convention used for ranking/aggregation where the
    # answer is a computed insight, not a list to page through). Cleared on
    # any other tool call, same as last_single_doc_link, so it only reflects
    # the MOST RECENT relevant action. Used to offer "Load More" pagination
    # on the final answer without a second Gemini call.
    last_list_call = None

    for round_num in range(5):
        gen_config_kwargs = {"tools": tools}
        if is_extraction:
            # Near-zero temperature for deterministic, repeatable field extraction --
            # the same invoice image should read the same numbers every time, not vary
            # run to run on values that get inserted straight into a Purchase Invoice.
            gen_config_kwargs["temperature"] = 0.1
        response = _gemini_generate_with_rotation(
            gemini_keys, model, contents, types.GenerateContentConfig(**gen_config_kwargs)
        )

        function_call = None
        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call:
                function_call = part.function_call
                break

        if not function_call:
            result_text = response.text or ""

            # More rows exist beyond the first page — hand the frontend the
            # query recipe (doctype/filters/fields) plus the first 10 rows so
            # it can build the table itself and offer "Load More", which
            # re-runs this same query directly via MCP with NO Gemini call.
            # ONLY when the caller opted in (ask_document's native widget) —
            # _call_gemini is shared with chat_api, which has its own JSON
            # contract and no handling for this sentinel; wrapping this for
            # chat_api too would break external callers' responses.
            if enable_list_pagination and last_list_call and len(last_list_call["rows"]) > 10:
                summary_lines = [
                    ln for ln in result_text.split("\n") if not ln.strip().startswith("|")
                ]
                summary_text = "\n".join(summary_lines).strip()
                return "LIST_DATA:" + json.dumps({
                    "doctype": last_list_call["doctype"],
                    "filters": last_list_call["filters"],
                    "fields": last_list_call["fields"],
                    "total": last_list_call["total"],
                    "rows": last_list_call["rows"][:10],
                    "summary": summary_text,
                })

            doc_link = last_single_doc_link
            if not doc_link and known_doc_names:
                # Fallback: no fresh single-record tool call this round, but
                # the final answer text names exactly ONE of the documents
                # fetched anywhere this turn — e.g. "which invoice has the
                # highest total?" fetches a whole list via list_documents,
                # then answers naming just the one with the highest total.
                # If the text names more than one fetched document, skip —
                # that means the answer is still listing/comparing multiple
                # records, not settled on a single one.
                mentioned = [n for n in known_doc_names if n in result_text]
                if len(mentioned) == 1:
                    doc_link = {"doctype": known_doc_names[mentioned[0]], "name": mentioned[0]}
            if doc_link:
                # Response STAYS a plain string, exactly as before — the link
                # is appended as a plain Markdown line, never a structural
                # change to what this function returns.
                doc_url = f"/app/{doc_link['doctype'].lower().replace(' ', '-')}/{doc_link['name']}"
                result_text = f"{result_text}\n\n[Open {doc_link['name']} →]({doc_url})"
            return result_text

        tool_name = function_call.name
        tool_args = dict(function_call.args)

        if tool_name == "preview_purchase_invoice":
            mapped, validation_error = _validate_mapped_data(tool_args.get("mapped_data", "{}"))
            return "PI_PREVIEW:" + json.dumps({
                "mapped_data": mapped,
                "summary": tool_args.get("summary", ""),
                "validation_error": validation_error,
            })

        if tool_name == "preview_quotation":
            mapped, validation_error = _validate_quotation_data(tool_args.get("mapped_data", "{}"))
            return "QUOTATION_PREVIEW:" + json.dumps({
                "mapped_data": mapped,
                "summary": tool_args.get("summary", ""),
                "validation_error": validation_error,
            })

        mcp_data = call_mcp(tool_name, tool_args)
        if tool_name in ("list_documents", "get_document"):
            doctype = tool_args.get("doctype")
            last_single_doc_link = _extract_single_doc_link(doctype, mcp_data)
            for _name in _extract_doc_names(doctype, mcp_data):
                known_doc_names[_name] = doctype
        else:
            last_single_doc_link = None

        if tool_name == "list_documents" and (tool_args.get("limit") or 0) <= 50:
            rows, _unused_count = _extract_list_rows(mcp_data)
            requested_limit = tool_args.get("limit") or 0
            reliable_total = len(rows) if len(rows) < requested_limit else None
            last_list_call = {
                "doctype": tool_args.get("doctype"),
                "filters": tool_args.get("filters") or {},
                "fields": tool_args.get("fields") or [],
                "rows": rows,
                "total": reliable_total,
            } if rows else None
        else:
            last_list_call = None

        contents.append(types.Content(role="model", parts=response.candidates[0].content.parts))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(
            text=f"Tool result:\n{mcp_data}\n\nNow answer the user's original question: {message}"
        )]))

    return "Could not complete the request."


def _call_claude(message, system_prompt, history_list, call_mcp, image_data=None, image_mime=None):
    import json
    import base64
    import anthropic

    api_key = frappe.conf.get("claude_api_key")
    if not api_key:
        frappe.throw(_("claude_api_key not configured in site_config.json"))

    model = frappe.conf.get("claude_model") or "claude-haiku-4-5-20251001"
    client = anthropic.Anthropic(api_key=api_key)

    messages = []
    for h in history_list:
        role = "assistant" if h.get("role") == "model" else h.get("role", "user")
        messages.append({"role": role, "content": h.get("content", "")})

    # Current user message (Claude supports JPEG/PNG/GIF/WEBP only — no PDF)
    _CLAUDE_IMG_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    user_content = []
    if image_data and image_mime and image_mime in _CLAUDE_IMG_TYPES:
        user_content.append({"type": "image", "source": {"type": "base64", "media_type": image_mime, "data": image_data}})
    user_content.append({"type": "text", "text": message})
    messages.append({"role": "user", "content": user_content})

    for _ in range(5):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=_claude_tools(),
            messages=messages
        )

        if response.stop_reason != "tool_use":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        # Handle ALL tool_use blocks — Claude requires a tool_result for every one
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            break

        # Check for preview_purchase_invoice first
        for tb in tool_use_blocks:
            if tb.name == "preview_purchase_invoice":
                mapped, validation_error = _validate_mapped_data(tb.input.get("mapped_data", "{}"))
                return "PI_PREVIEW:" + json.dumps({
                    "mapped_data": mapped,
                    "summary": tb.input.get("summary", ""),
                    "validation_error": validation_error,
                })

        # Call MCP for all tool_use blocks and collect results
        tool_results = []
        for tb in tool_use_blocks:
            mcp_result = call_mcp(tb.name, tb.input)
            tool_results.append({"type": "tool_result", "tool_use_id": tb.id, "content": mcp_result})

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return "Could not complete the request."


def _call_openai_compatible(message, system_prompt, history_list, call_mcp, image_data=None, image_mime=None, provider="openrouter"):
    import json
    import requests

    provider_config = {
        "openrouter": ("openrouter_api_key", "openrouter_model", "nvidia/nemotron-3-nano-30b-a3b:free", "https://openrouter.ai/api/v1/chat/completions"),
        "kimi":       ("kimi_api_key",       "kimi_model",       "moonshot-v1-8k",                      "https://api.moonshot.cn/v1/chat/completions"),
        "glm":        ("glm_api_key",        "glm_model",        "glm-4",                               "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
        "deepseek":   ("deepseek_api_key",   "deepseek_model",   "deepseek-chat",                       "https://api.deepseek.com/chat/completions"),
    }
    key_field, model_field, default_model, base_url = provider_config[provider]

    api_key = frappe.conf.get(key_field)
    if not api_key:
        frappe.throw(_(f"{key_field} not configured in site_config.json"))

    model = frappe.conf.get(model_field) or default_model

    messages = [{"role": "system", "content": system_prompt}]
    for h in history_list:
        role = "assistant" if h.get("role") == "model" else h.get("role", "user")
        messages.append({"role": role, "content": h.get("content", "")})

    # Current user message
    if image_data and image_mime:
        messages.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_data}"}},
            {"type": "text", "text": message}
        ]})
    else:
        messages.append({"role": "user", "content": message})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for _ in range(5):
        resp = requests.post(base_url, headers=headers,
                             json={"model": model, "messages": messages, "tools": _oai_tools()},
                             timeout=60)
        if resp.status_code != 200:
            return f"API error: {resp.text}"

        data = resp.json()
        choice = data["choices"][0]

        if choice.get("finish_reason") != "tool_calls":
            result = choice["message"].get("content", "") or ""
            return result

        tool_calls = choice["message"].get("tool_calls", [])
        if not tool_calls:
            break

        messages.append(choice["message"])

        # Check for preview_purchase_invoice first
        for tc in tool_calls:
            if tc["function"]["name"] == "preview_purchase_invoice":
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except Exception:
                    tool_args = {}
                mapped, validation_error = _validate_mapped_data(tool_args.get("mapped_data", "{}"))
                return "PI_PREVIEW:" + json.dumps({
                    "mapped_data": mapped,
                    "summary": tool_args.get("summary", ""),
                    "validation_error": validation_error,
                })

        # Call MCP for all tool calls and collect results
        tool_results = []
        for tc in tool_calls:
            try:
                tool_args = json.loads(tc["function"]["arguments"])
            except Exception:
                tool_args = {}
            mcp_result = call_mcp(tc["function"]["name"], tool_args)
            tool_results.append({"role": "tool", "tool_call_id": tc["id"], "content": mcp_result})

        messages.extend(tool_results)

    return "Could not complete the request."


def _call_bedrock(message, system_prompt, history_list, call_mcp, image_data=None, image_mime=None):
    import json
    import base64
    import boto3

    access_key = frappe.conf.get("bedrock_access_key")
    secret_key = frappe.conf.get("bedrock_secret_key")
    if not access_key or not secret_key:
        frappe.throw(_("bedrock_access_key and bedrock_secret_key not configured in site_config.json"))

    region = frappe.conf.get("bedrock_region") or "us-east-1"
    model  = frappe.conf.get("bedrock_model")  or "anthropic.claude-3-haiku-20240307-v1:0"

    client = boto3.client("bedrock-runtime",
                          aws_access_key_id=access_key,
                          aws_secret_access_key=secret_key,
                          region_name=region)

    messages = []
    for h in history_list:
        role = "assistant" if h.get("role") == "model" else h.get("role", "user")
        messages.append({"role": role, "content": [{"text": h.get("content", "")}]})

    # Current user message
    user_content = []
    if image_data and image_mime:
        fmt = image_mime.split("/")[-1]
        user_content.append({"image": {"format": fmt, "source": {"bytes": base64.b64decode(image_data)}}})
    user_content.append({"text": message})
    messages.append({"role": "user", "content": user_content})

    for _ in range(5):
        resp = client.converse(
            modelId=model,
            messages=messages,
            system=[{"text": system_prompt}],
            toolConfig={"tools": _bedrock_tools()}
        )

        stop_reason = resp["stopReason"]
        output_msg = resp["output"]["message"]

        if stop_reason != "tool_use":
            for block in output_msg["content"]:
                if "text" in block:
                    return block["text"]
            return ""

        messages.append(output_msg)
        tool_results = []

        for block in output_msg["content"]:
            if "toolUse" not in block:
                continue
            tool_use = block["toolUse"]
            tool_name = tool_use["name"]
            tool_args = tool_use["input"]

            if tool_name == "preview_purchase_invoice":
                mapped, validation_error = _validate_mapped_data(tool_args.get("mapped_data", "{}"))
                return "PI_PREVIEW:" + json.dumps({
                    "mapped_data": mapped,
                    "summary": tool_args.get("summary", ""),
                    "validation_error": validation_error,
                })

            mcp_result = call_mcp(tool_name, tool_args)
            tool_results.append({"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"text": mcp_result}]}})

        messages.append({"role": "user", "content": tool_results})

    return "Could not complete the request."


def _ai_single_completion(prompt):
    """Single-shot, tool-free completion: send one prompt, get raw text back.
    Routes to whichever ai_provider is configured in site_config.json —
    same provider selection ask_document/chat_api already use — so a plain
    mapping/extraction call isn't hard-coupled to one vendor's SDK the way
    _call_gemini's full tool-calling loop is."""
    ai_provider = _ai_conf("ai_provider") or "gemini"

    if ai_provider == "gemini":
        gemini_keys = _gemini_key_list()
        if not gemini_keys:
            frappe.throw(_("gemini_api_keys not configured in site_config.json"))
        model = _ai_conf("gemini_model") or "gemini-flash-lite-latest"
        from google.genai import types
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        response = _gemini_generate_with_rotation(
            gemini_keys, model, contents, types.GenerateContentConfig(temperature=0.1)
        )
        return response.text or ""

    if ai_provider == "claude":
        import anthropic
        api_key = frappe.conf.get("claude_api_key")
        if not api_key:
            frappe.throw(_("claude_api_key not configured in site_config.json"))
        model = frappe.conf.get("claude_model") or "claude-haiku-4-5-20251001"
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=4096, messages=[{"role": "user", "content": prompt}]
        )
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    if ai_provider in ("openrouter", "kimi", "glm", "deepseek"):
        import requests
        provider_config = {
            "openrouter": ("openrouter_api_key", "openrouter_model", "nvidia/nemotron-3-nano-30b-a3b:free", "https://openrouter.ai/api/v1/chat/completions"),
            "kimi":       ("kimi_api_key",       "kimi_model",       "moonshot-v1-8k",                      "https://api.moonshot.cn/v1/chat/completions"),
            "glm":        ("glm_api_key",        "glm_model",        "glm-4",                               "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
            "deepseek":   ("deepseek_api_key",   "deepseek_model",   "deepseek-chat",                       "https://api.deepseek.com/chat/completions"),
        }
        key_field, model_field, default_model, base_url = provider_config[ai_provider]
        api_key = frappe.conf.get(key_field)
        if not api_key:
            frappe.throw(_(f"{key_field} not configured in site_config.json"))
        model = frappe.conf.get(model_field) or default_model
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = requests.post(
            base_url, headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=60
        )
        if resp.status_code != 200:
            frappe.throw(_(f"AI provider error: {resp.text}"))
        return resp.json()["choices"][0]["message"].get("content", "") or ""

    if ai_provider == "bedrock":
        import boto3
        access_key = frappe.conf.get("bedrock_access_key")
        secret_key = frappe.conf.get("bedrock_secret_key")
        if not access_key or not secret_key:
            frappe.throw(_("bedrock_access_key and bedrock_secret_key not configured in site_config.json"))
        region = frappe.conf.get("bedrock_region") or "us-east-1"
        model  = frappe.conf.get("bedrock_model")  or "anthropic.claude-3-haiku-20240307-v1:0"
        client = boto3.client("bedrock-runtime",
                              aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key,
                              region_name=region)
        resp = client.converse(modelId=model, messages=[{"role": "user", "content": [{"text": prompt}]}])
        for block in resp["output"]["message"]["content"]:
            if "text" in block:
                return block["text"]
        return ""

    frappe.throw(_(f"Unknown ai_provider '{ai_provider}'. Valid: gemini, claude, openrouter, bedrock, kimi, glm, deepseek"))


# ─── Main entry point (router) ────────────────────────────────────────────────

@frappe.whitelist()
def ask_document(message, doctype=None, docname=None, image_data=None, image_mime=None, history=None, selected_role=None):
    import json
    import requests
    from datetime import date
    from frappe.utils.password import get_decrypted_password

    ai_provider = _ai_conf("ai_provider") or "gemini"

    current_user   = frappe.session.user

    assistant_allowed, assistant_deny_message = _check_assistant_access(current_user)
    if not assistant_allowed:
        return assistant_deny_message

    role_based_mode_on, role_based_token, role_choices = _get_role_based_mcp_token(
        current_user, selected_role=selected_role, allow_role_prompt=True
    )
    if role_based_mode_on and role_choices:
        return "ROLE_SELECT:" + json.dumps({"roles": role_choices})
    if role_based_mode_on and not role_based_token:
        return _("You don't have access to this. Please contact admin.")

    if role_based_mode_on:
        fac_token = role_based_token
    else:
        fac_api_key    = frappe.db.get_value("User", current_user, "api_key")
        fac_api_secret = None
        if fac_api_key:
            try:
                fac_api_secret = get_decrypted_password("User", current_user, "api_secret")
            except Exception:
                fac_api_secret = None
        fac_token = f"{fac_api_key}:{fac_api_secret}" if fac_api_key and fac_api_secret else None

    def call_mcp(tool_name, tool_args):
        if not fac_token:
            return "Error: MCP credentials not available for this user."
        resp = requests.post(
            _mcp_url(),
            headers={"Authorization": f"token {fac_token}", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}, "id": 1},
            timeout=30
        )
        if resp.status_code == 401:
            return "Error: Invalid or expired API credentials. Access denied."
        if resp.status_code == 403:
            return f"Error: You do not have permission to access '{tool_args.get('doctype', tool_name)}'."
        if resp.status_code == 404:
            return "Error: MCP integration (frappe_assistant_core) is not installed on this site. Please contact admin."
        if resp.status_code != 200:
            return f"Error: MCP server returned status {resp.status_code}."
        content = resp.json().get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
        return "Error: No data returned from server."

    # Build system prompt
    today = date.today()
    current_month_start = today.replace(day=1).strftime("%Y-%m-%d")
    current_month_end   = today.strftime("%Y-%m-%d")

    current_doc_context = ""
    if doctype and docname and fac_token:
        try:
            pre_load = requests.post(
                _mcp_url(),
                headers={"Authorization": f"token {fac_token}", "Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_document", "arguments": {"doctype": doctype, "name": docname}}, "id": 0},
                timeout=15
            )
            if pre_load.status_code == 200:
                pre_result = pre_load.json().get("result", {}).get("content", [])
                if pre_result:
                    current_doc_context = f"\nCurrently open document ({doctype}: {docname}):\n{pre_result[0].get('text', '')}\n"
        except Exception:
            pass

    system_prompt = (
        f"You are an ERPNext AI assistant. Today's date is {today}. "
        f"Current month: {current_month_start} to {current_month_end}. "
        "Tool selection rules: "
        "1. For totals/summaries use generate_report with a known report name. "
        "2. For top-N/ranking/aggregation use list_documents with fields=['relevant_fields'] and limit=1000, then rank/sum yourself — NEVER loop get_document. "
        "3. For listing records use list_documents with fields=['fields_you_need'] and limit=50. "
        "4. ALWAYS pass 'fields' to list_documents — omitting it returns only name/creation/modified. "
        "5. For 'low stock' / 'items below reorder level': Item does NOT have actual_qty, "
        "projected_qty, or reorder_level fields directly. Use list_documents on doctype='Bin' "
        "with fields=['item_code', 'warehouse', 'actual_qty', 'reserved_qty', 'projected_qty'] and "
        "limit=1000 to get current stock per item/warehouse, and separately use list_documents on "
        "doctype='Item' with fields=['item_code', 'reorder_levels'] to get each item's reorder "
        "threshold (a child table with 'warehouse_reorder_level'). Match them by item_code and "
        "compare actual_qty to warehouse_reorder_level yourself. "
        "Always use LIKE filter for names: {{'customer_name': ['like', '%name%']}}. "
        "For list responses, show maximum 10 records. If there are more, mention the total count and say 'showing first 10'. "
        "Formatting: whenever a response has more than one record or more than two fields per record, "
        "format it as a proper Markdown table using this EXACT structure, with a real newline character "
        "after every row (never join rows with '||' or put them on one line):\n"
        "| Column A | Column B |\n"
        "|---|---|\n"
        "| value 1 | value 2 |\n"
        "| value 3 | value 4 |\n"
        "Each of the 4 lines above is its own separate line ending in \\n. Do not use a table for a "
        "single scalar answer (e.g. a plain count or one value)."
        f"{current_doc_context}"
    )

    # Parse history
    history_list = []
    if history:
        try:
            history_list = json.loads(history)
        except Exception:
            pass

    # If uploaded file is a JPEG/PNG, convert to OCR PDF before sending to AI.
    # ocrmypdf deskews phone photos and adds a text layer → more accurate extraction.
    _IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png"}
    if image_data and image_mime and image_mime in _IMAGE_MIMES:
        import base64 as _b64
        import io as _io
        import os as _os
        import tempfile as _tmp
        try:
            import img2pdf as _img2pdf
            _ext = ".png" if image_mime == "image/png" else ".jpg"
            with _tmp.NamedTemporaryFile(suffix=_ext, delete=False) as _f:
                _f.write(_b64.b64decode(image_data))
                _tmp_path = _f.name
            try:
                _pdf_bytes = _img2pdf.convert(_tmp_path)
                try:
                    import ocrmypdf as _ocr
                    _in  = _io.BytesIO(_pdf_bytes)
                    _out = _io.BytesIO()
                    _ocr.ocr(_in, _out, deskew=True, force_ocr=True, progress_bar=False)
                    _pdf_bytes = _out.getvalue()
                except Exception:
                    pass  # OCR failed — use plain img2pdf output
                image_data = _b64.b64encode(_pdf_bytes).decode("utf-8")
                image_mime = "application/pdf"
                print("AI image→PDF: conversion successful, sending PDF to AI")
            finally:
                _os.unlink(_tmp_path)
        except Exception:
            print("AI image→PDF: conversion failed, sending original image to AI")

    # Route to provider
    kwargs = dict(message=message, system_prompt=system_prompt, history_list=history_list,
                  call_mcp=call_mcp, image_data=image_data, image_mime=image_mime)

    if ai_provider == "gemini":
        return _call_gemini(**kwargs, enable_list_pagination=True)
    elif ai_provider == "claude":
        return _call_claude(**kwargs)
    elif ai_provider in ("openrouter", "kimi", "glm", "deepseek"):
        return _call_openai_compatible(**kwargs, provider=ai_provider)
    elif ai_provider == "bedrock":
        return _call_bedrock(**kwargs)
    else:
        frappe.throw(_(f"Unknown ai_provider '{ai_provider}'. Valid: gemini, claude, openrouter, bedrock, kimi, glm, deepseek"))


# ─── "Load More" pagination for LIST_DATA responses ───────────────────────────
# Re-runs the SAME query (doctype/filters/fields) that Gemini already decided
# on for the first page — no AI reasoning needed for "give me more of the
# same list", so this never calls Gemini at all. Fetches limit=offset+page_size
# directly via MCP and returns just the new slice, since the list_documents
# MCP tool has no separate "offset" parameter to rely on.
@frappe.whitelist()
def load_more_list_records(doctype, filters, fields, offset, page_size=10, total=None):
    import json
    import requests
    from frappe.utils.password import get_decrypted_password

    current_user = frappe.session.user
    assistant_allowed, assistant_deny_message = _check_assistant_access(current_user)
    if not assistant_allowed:
        frappe.throw(assistant_deny_message)

    offset = int(offset)
    page_size = int(page_size)
    if isinstance(filters, str):
        filters = json.loads(filters or "{}")
    if isinstance(fields, str):
        fields = json.loads(fields or "[]")

    role_based_mode_on, role_based_token, _role_choices = _get_role_based_mcp_token(current_user)
    if role_based_mode_on and not role_based_token:
        frappe.throw(_("You don't have access to this. Please contact admin."))

    if role_based_mode_on:
        fac_token = role_based_token
    else:
        fac_api_key = frappe.db.get_value("User", current_user, "api_key")
        fac_api_secret = None
        if fac_api_key:
            try:
                fac_api_secret = get_decrypted_password("User", current_user, "api_secret")
            except Exception:
                fac_api_secret = None
        fac_token = f"{fac_api_key}:{fac_api_secret}" if fac_api_key and fac_api_secret else None

    if not fac_token:
        frappe.throw(_("MCP credentials not available for this user."))

    fetch_limit = offset + page_size + 1
    resp = requests.post(
        _mcp_url(),
        headers={"Authorization": f"token {fac_token}", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": "list_documents",
            "arguments": {"doctype": doctype, "filters": filters, "fields": fields, "limit": fetch_limit}
        }, "id": 1},
        timeout=30
    )
    if resp.status_code != 200:
        frappe.throw(_(f"MCP server returned status {resp.status_code}."))

    content = resp.json().get("result", {}).get("content", [])
    mcp_data = content[0].get("text", "") if content else ""
    rows, _unused_fetched_total = _extract_list_rows(mcp_data)

    next_rows = rows[offset:offset + page_size]
    has_more = len(rows) > offset + len(next_rows)

    return {
        "rows": next_rows,
        # Only a genuinely reliable number once we've actually reached the
        # give, so the frontend shows "shown so far" instead of a guess.
        "total": (offset + len(next_rows)) if not has_more else None,
        "has_more": has_more,
    }


# ─── External / API chat endpoint ────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
def chat_api(message, conversation_history=None):
    """
    Stateless REST endpoint for external callers.
    Auth: Authorization: token <api_key>:<api_secret>
    Payload:
        message               str  — user message
        conversation_history  str  — optional JSON array of prior turns [{role, content}, ...]
    Response:
        { "success": true, "response": {...}, "conversation_history": [...] }
    """
    import requests
    from datetime import date
    from frappe.utils.password import get_decrypted_password

    if not message:
        frappe.throw(_("message is required"))

    ai_provider = _ai_conf("ai_provider") or "gemini"

    current_user   = frappe.session.user

    assistant_allowed, assistant_deny_message = _check_assistant_access(current_user)
    if not assistant_allowed:
        return {"success": False, "error": {"code": "ACCESS_DENIED", "message": assistant_deny_message}}

    # allow_role_prompt not passed here (defaults False) — chat_api has no
    # UI to prompt from, so a 2+-role match keeps falling back to the
    # caller's own credentials, unchanged from before.
    role_based_mode_on, role_based_token, _role_choices = _get_role_based_mcp_token(current_user)
    if role_based_mode_on and not role_based_token:
        return {"success": False, "error": {"code": "ACCESS_DENIED", "message": _("You don't have access to this. Please contact admin.")}}

    if role_based_mode_on:
        fac_token = role_based_token
    else:
        fac_api_key    = frappe.db.get_value("User", current_user, "api_key")
        fac_api_secret = None
        if fac_api_key:
            try:
                fac_api_secret = get_decrypted_password("User", current_user, "api_secret")
            except Exception:
                fac_api_secret = None
        fac_token = f"{fac_api_key}:{fac_api_secret}" if fac_api_key and fac_api_secret else None

    def call_mcp(tool_name, tool_args):
        if not fac_token:
            return "Error: MCP credentials not available for this user."
        resp = requests.post(
            _mcp_url(),
            headers={"Authorization": f"token {fac_token}", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}, "id": 1},
            timeout=30
        )
        if resp.status_code == 401:
            return "Error: Invalid or expired API credentials. Access denied."
        if resp.status_code == 403:
            return f"Error: You do not have permission to access '{tool_args.get('doctype', tool_name)}'."
        if resp.status_code == 404:
            return "Error: MCP integration (frappe_assistant_core) is not installed on this site. Please contact admin."
        if resp.status_code != 200:
            return f"Error: MCP server returned status {resp.status_code}."
        content = resp.json().get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
        return "Error: No data returned from server."

    today = date.today()
    current_month_start = today.replace(day=1).strftime("%Y-%m-%d")
    current_month_end   = today.strftime("%Y-%m-%d")

    system_prompt = (
        f"You are an ERPNext AI assistant. Today's date is {today}. "
        f"Current month: {current_month_start} to {current_month_end}. "
        "Tool selection rules: "
        "1. For totals/summaries use generate_report with a known report name. "
        "2. For top-N/ranking/aggregation use list_documents with fields=['relevant_fields'] and limit=1000, then rank/sum yourself — NEVER loop get_document. "
        "3. For listing records use list_documents with fields=['fields_you_need'] and limit=50. "
        "4. ALWAYS pass 'fields' to list_documents — omitting it returns only name/creation/modified. "
        "5. For 'low stock' / 'items below reorder level': Item does NOT have actual_qty, "
        "projected_qty, or reorder_level fields directly. Use list_documents on doctype='Bin' "
        "with fields=['item_code', 'warehouse', 'actual_qty', 'reserved_qty', 'projected_qty'] and "
        "limit=1000 to get current stock per item/warehouse, and separately use list_documents on "
        "doctype='Item' with fields=['item_code', 'reorder_levels'] to get each item's reorder "
        "threshold (a child table with 'warehouse_reorder_level'). Match them by item_code and "
        "compare actual_qty to warehouse_reorder_level yourself. "
        "Always use LIKE filter for names: {{'customer_name': ['like', '%name%']}}. "
        "IMPORTANT: Always respond with valid JSON only. No markdown, no plain text. "
        "Choose the structure dynamically based on what the response contains: "
        "- List of records: {\"type\": \"list\", \"total\": <total fetched>, \"returned\": <n>, \"has_more\": true/false, \"message\": \"<one line>\", \"summary\": \"<one line>\", \"data\": [{...}, ...]} "
        "- Single record: {\"type\": \"record\", \"message\": \"<one line>\", \"data\": {...}} "
        "- Aggregation/total/summary: {\"type\": \"summary\", \"message\": \"<one line, e.g. '3 quotations were created this month.'>\", \"data\": {\"label\": \"<label>\", \"value\": <value>, \"extra\": {...}}} "
        "- Conversational/greeting/no data: {\"type\": \"message\", \"content\": \"<text>\"} "
        "- Error or not found: {\"type\": \"error\", \"content\": \"<reason>\"} "
        "The 'message' field must always be a natural-language sentence describing the answer in plain English, in addition to the structured 'data' — never omit it for list/record/summary types. "
        "IMPORTANT: For list responses, include maximum 10 records in the data array. "
        "If total fetched exceeds 10, set has_more: true and mention the total count in summary. "
        "Never wrap in markdown code blocks. Return raw JSON only."
    )

    import json as _json

    # Parse incoming conversation history
    history_list = []
    if conversation_history:
        try:
            history_list = _json.loads(conversation_history) if isinstance(conversation_history, str) else conversation_history
        except Exception:
            history_list = []

    try:
        kwargs = dict(message=message, system_prompt=system_prompt, history_list=history_list,
                      call_mcp=call_mcp, image_data=None, image_mime=None)

        if ai_provider == "gemini":
            result = _call_gemini(**kwargs)
        elif ai_provider == "claude":
            result = _call_claude(**kwargs)
        elif ai_provider in ("openrouter", "kimi", "glm", "deepseek"):
            result = _call_openai_compatible(**kwargs, provider=ai_provider)
        elif ai_provider == "bedrock":
            result = _call_bedrock(**kwargs)
        else:
            return {"success": False, "error": {"code": "INVALID_PROVIDER", "message": f"Unknown ai_provider '{ai_provider}'"}}

        try:
            parsed = _json.loads(result)
        except Exception:
            parsed = {"type": "message", "content": result}

        if isinstance(parsed, dict) and not parsed.get("message"):
            parsed["message"] = parsed.get("content") or parsed.get("summary") or ""

        # Append this turn to history and return it to caller
        updated_history = history_list + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": result}
        ]

        return {"success": True, "response": parsed, "conversation_history": updated_history}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "chat_api error")
        return {"success": False, "error": {"code": "SERVER_ERROR", "message": str(e)}}


# ─── Raw field extraction (arbitrary JSON body → field preview, no image) ─────
@frappe.whitelist(allow_guest=False)
def extract_fields_from_message(**kwargs):
    import json
    import requests
    from frappe.utils.password import get_decrypted_password

    doctype = frappe.get_request_header("X-Doctype")
    if doctype not in ("Purchase Invoice", "Quotation"):
        frappe.throw(_("X-Doctype header must be 'Purchase Invoice' or 'Quotation'"))

    raw_data = {k: v for k, v in frappe.local.form_dict.items() if k != "cmd"}
    if not raw_data:
        frappe.throw(_("Request body must contain at least one field"))

    current_user = frappe.session.user
    assistant_allowed, assistant_deny_message = _check_assistant_access(current_user)
    if not assistant_allowed:
        return {"success": False, "error": {"code": "ACCESS_DENIED", "message": assistant_deny_message}}

    role_based_mode_on, role_based_token, _role_choices = _get_role_based_mcp_token(current_user)
    if role_based_mode_on and not role_based_token:
        return {"success": False, "error": {"code": "ACCESS_DENIED", "message": _("You don't have access to this. Please contact admin.")}}

    if role_based_mode_on:
        fac_token = role_based_token
    else:
        fac_api_key = frappe.db.get_value("User", current_user, "api_key")
        fac_api_secret = None
        if fac_api_key:
            try:
                fac_api_secret = get_decrypted_password("User", current_user, "api_secret")
            except Exception:
                fac_api_secret = None
        fac_token = f"{fac_api_key}:{fac_api_secret}" if fac_api_key and fac_api_secret else None

    if not fac_token:
        return {"success": False, "error": {"code": "ACCESS_DENIED", "message": _("MCP credentials not available for this user.")}}

    # STEP 1 — one MCP call to learn the real field names for this doctype.
    resp = requests.post(
        _mcp_url(),
        headers={"Authorization": f"token {fac_token}", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {
            "name": "get_doctype_info", "arguments": {"doctype": doctype}
        }, "id": 1},
        timeout=30
    )
    if resp.status_code != 200:
        return {"success": False, "error": {"code": "MCP_ERROR", "message": f"MCP server returned status {resp.status_code}."}}
    content = resp.json().get("result", {}).get("content", [])
    doctype_info_text = content[0].get("text", "") if content else ""

    prompt = (
        f"You are mapping externally-provided {doctype} data to real ERPNext field names.\n\n"
        f"Real field structure for '{doctype}' (from ERPNext's own schema):\n{doctype_info_text}\n\n"
        f"Raw input data, with arbitrary/unknown field names:\n{json.dumps(raw_data)}\n\n"
        "For EVERY key in the raw input data above, decide which real ERPNext field it maps to. "
        "Map a raw key to a real field name wherever a clear semantic match exists (e.g. a key "
        "meaning 'customer name' maps to 'customer_name' if that's a real field). If a raw key has "
        "no reasonable match to any real field, set real_field to null for it — do NOT invent a "
        "fieldname that isn't in the real field structure above, and never guess. "
        "Return ONLY a raw JSON array, one entry per raw input key, each shaped exactly as "
        '{"raw_key": "<the exact raw input key>", "real_field": "<real field name>" or null}. '
        "Include every raw key exactly once, in the same order as the input. No markdown, no "
        "explanation, no code fences — just the JSON array."
    )

    try:
        raw_text = (_ai_single_completion(prompt) or "").strip()
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "extract_fields_from_message: AI call failed")
        return {"success": False, "error": {"code": "AI_ERROR", "message": str(e)}}

    # Some providers wrap JSON in ```json fences despite being told not to.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()

    try:
        mapping_list = json.loads(raw_text)
    except Exception as e:
        return {"success": False, "error": {"code": "MAPPING_FAILED", "message": f"Could not parse mapped fields: {e}"}}

    if not isinstance(mapping_list, list):
        return {"success": False, "error": {"code": "MAPPING_FAILED", "message": "Mapped result wasn't a JSON array."}}

    mapping_by_raw_key = {}
    for entry in mapping_list:
        if isinstance(entry, dict) and entry.get("raw_key") in raw_data:
            mapping_by_raw_key[entry["raw_key"]] = entry.get("real_field")

    mapped_dict = {
        real_field: raw_data[raw_key]
        for raw_key, real_field in mapping_by_raw_key.items()
        if real_field
    }
    if doctype == "Purchase Invoice":
        mapped_dict, validation_error = _validate_mapped_data(mapped_dict)
    else:
        mapped_dict, validation_error = _validate_quotation_data(mapped_dict)

    fields = []
    for raw_key, value in raw_data.items():
        real_field = mapping_by_raw_key.get(raw_key)
        if real_field:
            fields.append({"field": real_field, "value": mapped_dict.get(real_field, value)})
        else:
            fields.append({"field": raw_key, "value": value})

    return {
        "success": True,
        "doctype": doctype,
        "fields": fields,
        "validation_error": validation_error,
    }


# ─── Purchase Invoice creation ────────────────────────────────────────────────

@frappe.whitelist()
def create_purchase_invoice_from_data(data):
    import json
    import requests
    from difflib import SequenceMatcher
    from frappe.utils.password import get_decrypted_password

    if not frappe.has_permission("Purchase Invoice", "create"):
        frappe.throw(_("You don't have permission to create a Purchase Invoice."), frappe.PermissionError)

    current_user    = frappe.session.user
    _mcp_api_key    = frappe.db.get_value("User", current_user, "api_key")
    _mcp_api_secret = None
    if _mcp_api_key:
        try:
            _mcp_api_secret = get_decrypted_password("User", current_user, "api_secret")
        except Exception:
            _mcp_api_secret = None

    def _call_mcp(tool_name, tool_args):
        fac_api_key    = _mcp_api_key
        fac_api_secret = _mcp_api_secret
        try:
            resp = requests.post(
                _mcp_url(),
                headers={"Authorization": f"token {fac_api_key}:{fac_api_secret}", "Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}, "id": 1},
                timeout=30
            )
            if resp.status_code == 200:
                content = resp.json().get("result", {}).get("content", [])
                if content and isinstance(content, list):
                    return content[0].get("text", "")
        except Exception:
            pass
        return ""

    def _similarity(a, b):
        a, b = a.lower().strip(), b.lower().strip()
        r1 = SequenceMatcher(None, a, b).ratio()
        r2 = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
        return max(r1, r2)

    def _to_float_safe(v):
        # Tolerates OCR-style formatted numbers ("₹1,234.50") — never raises.
        if isinstance(v, (int, float)):
            return float(v), True
        import re as _re
        s = _re.sub(r"[^\d.\-]", "", str(v or ""))
        if s in ("", "-", "."):
            return 0.0, False
        try:
            return float(s), True
        except Exception:
            return 0.0, False

    try:
        parsed = json.loads(data)
        pi_data = parsed.get("mapped_data", parsed)

        items = pi_data.get("items", [])
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                items = []

        # Collect all non-empty item names from image
        # Deliberately skip item.get("name") — that field holds row numbers from the invoice table,
        # not the product name, and causes rows like {"name": "1"} to create items named "1".
        items_to_match = []
        for item in items:
            item_name_raw = (
                item.get("item_name") or
                item.get("item_code") or
                item.get("description") or
                ""
            ).strip()
            if item_name_raw:
                items_to_match.append((item_name_raw, item))

        not_found    = []
        matched_rows = []

        if items_to_match:
            # ONE MCP call — fetch all active items from production
            mcp_result = _call_mcp("list_documents", {
                "doctype": "Item",
                "filters": {"disabled": 0},
                "fields": ["name", "item_name", "stock_uom"],
                "limit": 1000
            })
            item_master = []
            try:
                parsed_result = json.loads(mcp_result or "[]")
                if isinstance(parsed_result, list):
                    item_master = parsed_result
                elif isinstance(parsed_result, dict):
                    # MCP wraps as {"success": true, "result": {"data": [...]}}
                    inner = parsed_result.get("result", parsed_result)
                    if isinstance(inner, list):
                        item_master = inner
                    elif isinstance(inner, dict):
                        item_master = inner.get("data", inner.get("message", []))
                    if not item_master:
                        item_master = parsed_result.get("data", parsed_result.get("message", []))
            except Exception:
                pass

            # Fuzzy-match every image item against full Item master list
            for item_name_raw, item in items_to_match:

                # Purely numeric item_name_raw means Gemini picked up a row/serial number — skip it.
                if item_name_raw.isdigit():
                    continue

                best, best_score = None, 0.0
                for candidate in item_master:
                    cname = candidate.get("item_name") or candidate.get("name") or ""
                    score = _similarity(item_name_raw, cname)
                    if score > best_score:
                        best_score = score
                        best = candidate

                if best and best_score >= _FUZZY_MATCH_THRESHOLD:
                    qty = float(item.get("qty", 1)) or 1.0
                    rate = float(item.get("rate", 0))
                    total_amt, total_ok   = _to_float_safe(item.get("total_amount") or item.get("amount"))
                    discount_amt, disc_ok = _to_float_safe(item.get("discount_amount"))
                    if total_ok and total_amt > 0 and qty > 0:
                        if disc_ok and discount_amt > 0:
                            # total_amount is pre-discount (printed "Total" column);
                            # subtract discount to get the actual taxable rate.
                            rate = (total_amt - discount_amt) / qty
                        else:
                            # No discount — total_amount IS the taxable line total.
                            rate = total_amt / qty
                    elif disc_ok and discount_amt > 0:
                        # No total_amount extracted — fall back to rate minus discount.
                        rate = max(rate - (discount_amt / qty), 0.0)
                    item_row = {
                        "item_code":   best.get("name", item_name_raw),
                        "item_name":   best.get("item_name", item_name_raw),
                        "description": best.get("item_name", item_name_raw),
                        "qty":         qty,
                        "rate":        rate,
                        "uom":         item.get("uom") or best.get("stock_uom") or "Nos",
                    }
                    skip_item_fields = {"item_code", "item_name", "description", "qty", "rate", "uom", "discount_amount"}
                    for k, v in item.items():
                        if k not in skip_item_fields and v and not isinstance(v, (list, dict)):
                            item_row[k] = v
                    matched_rows.append(item_row)
                else:
                    not_found.append(item_name_raw)

        raw_supplier_name = (pi_data.get("supplier") or pi_data.get("supplier_name") or "").strip()
        if raw_supplier_name:
            supplier_result = _call_mcp("list_documents", {
                "doctype": "Supplier",
                "filters": {"disabled": 0},
                "fields": ["name", "supplier_name"],
                "limit": 1000
            })
            supplier_master = []
            try:
                parsed_supplier = json.loads(supplier_result or "[]")
                if isinstance(parsed_supplier, list):
                    supplier_master = parsed_supplier
                elif isinstance(parsed_supplier, dict):
                    inner = parsed_supplier.get("result", parsed_supplier)
                    if isinstance(inner, list):
                        supplier_master = inner
                    elif isinstance(inner, dict):
                        supplier_master = inner.get("data", inner.get("message", []))
                    if not supplier_master:
                        supplier_master = parsed_supplier.get("data", parsed_supplier.get("message", []))
            except Exception:
                pass

            best_supplier, best_supplier_score = None, 0.0
            for candidate in supplier_master:
                cname = candidate.get("supplier_name") or candidate.get("name") or ""
                score = _similarity(raw_supplier_name, cname)
                if score > best_supplier_score:
                    best_supplier_score = score
                    best_supplier = candidate

            if best_supplier and best_supplier_score >= _FUZZY_MATCH_THRESHOLD:
                pi_data["supplier"] = best_supplier.get("name")
                pi_data["supplier_name"] = best_supplier.get("supplier_name") or best_supplier.get("name")
            else:
                not_found.append(f"Supplier: {raw_supplier_name}")

        if not_found:
            return {
                "status": "items_not_found",
                "not_found": not_found,
                "message": f"These records were not found in ERPNext: {', '.join(not_found)}. Please add them first or correct the name(s)."
            }

        # ─── Match company against the real Company master — NON-BLOCKING. Only
        # override when there's a confident match, for multi-company sites where
        # the invoice's Bill To is a different entity/branch than the uploader's
        # personal default. If not extracted or not confidently matched, do
        # nothing and let ERPNext's own default-company behavior apply exactly
        # as it already does today.
        raw_company_name = (pi_data.get("company") or "").strip()
        if raw_company_name:
            company_result = _call_mcp("list_documents", {
                "doctype": "Company",
                "fields": ["name"],
                "limit": 100
            })
            company_master = []
            try:
                parsed_company = json.loads(company_result or "[]")
                if isinstance(parsed_company, list):
                    company_master = parsed_company
                elif isinstance(parsed_company, dict):
                    inner = parsed_company.get("result", parsed_company)
                    if isinstance(inner, list):
                        company_master = inner
                    elif isinstance(inner, dict):
                        company_master = inner.get("data", inner.get("message", []))
                    if not company_master:
                        company_master = parsed_company.get("data", parsed_company.get("message", []))
            except Exception:
                pass

            best_company, best_company_score = None, 0.0
            for candidate in company_master:
                cname = candidate.get("name") or ""
                score = _similarity(raw_company_name, cname)
                if score > best_company_score:
                    best_company_score = score
                    best_company = candidate

            if best_company and best_company_score >= _FUZZY_MATCH_THRESHOLD:
                pi_data["company"] = best_company.get("name")
            else:
                pi_data.pop("company", None)

        doc = frappe.new_doc("Purchase Invoice")

        skip_fields = {"items", "taxes", "taxes_and_charges", "payment_schedule",
                       "doctype", "name", "owner", "creation", "modified"}
        _non_data_fieldtypes = {"Section Break", "Column Break", "Tab Break", "Fold",
                                 "HTML", "Button", "Heading", "Table", "Table MultiSelect"}
        _valid_fieldnames = {df.fieldname for df in doc.meta.fields if df.fieldtype not in _non_data_fieldtypes}
        _unmapped_fields, _failed_fields = [], []
        for fieldname, value in pi_data.items():
            if fieldname not in skip_fields and value:
                if isinstance(value, (list, dict)):
                    continue
                if fieldname not in _valid_fieldnames:
                    _unmapped_fields.append((fieldname, value))
                    continue
                try:
                    setattr(doc, fieldname, value)
                except Exception:
                    _failed_fields.append((fieldname, value))
        if _unmapped_fields or _failed_fields:
            frappe.log_error(
                title="AI PI: field(s) dropped",
                message=(
                    f"Not a real Purchase Invoice field (would otherwise be silently dropped by "
                    f"Frappe on insert with no trace): {_unmapped_fields}\n"
                    f"setattr raised an exception: {_failed_fields}"
                ),
            )

        for item_row in matched_rows:
            doc.append("items", item_row)

        # ─── Payment Schedule (payment terms) — a child table, can't come through the
        # generic scalar loop above (table-shaped values are skipped there). Build it
        # explicitly, the same way items/taxes already are. Works for any invoice with
        # payment_terms/due_date printed, whether given as one row or a full schedule.
        payment_schedule = pi_data.get("payment_schedule")
        if not payment_schedule:
            single_due_date = pi_data.get("due_date")
            single_term = pi_data.get("payment_terms")
            if single_due_date or single_term:
                payment_schedule = [{
                    "payment_term": single_term,
                    "due_date": single_due_date,
                    "invoice_portion": 100,
                }]
        if isinstance(payment_schedule, str):
            try:
                payment_schedule = json.loads(payment_schedule)
            except Exception:
                payment_schedule = []
        if not isinstance(payment_schedule, list):
            payment_schedule = []

        for ps_row in payment_schedule:
            if not isinstance(ps_row, dict):
                continue
            row = {}
            if ps_row.get("payment_term"):
                row["payment_term"] = ps_row["payment_term"]
            if ps_row.get("due_date"):
                try:
                    row["due_date"] = frappe.utils.getdate(ps_row["due_date"])
                except Exception:
                    pass  # skip an unparseable date rather than aborting the whole invoice
            for k in ("invoice_portion", "payment_amount"):
                val, ok = _to_float_safe(ps_row.get(k))
                if ok and val:
                    row[k] = val
            if ps_row.get("description"):
                row["description"] = ps_row["description"]
            if row:
                try:
                    doc.append("payment_schedule", row)
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "AI PI: payment_schedule row failed")

        if not doc.items:
            doc.append("items", {"item_name": "Item from vendor invoice", "description": "Please update item details", "qty": 1, "rate": 0, "uom": "Nos"})

        if doc.supplier and not doc.supplier_name:
            doc.supplier_name = doc.supplier

        # ─── Apply the ACTUAL tax rows Gemini extracted from the invoice image ───
        # (previously: "taxes" was skipped entirely by skip_fields and a random default
        # tax template was applied instead, regardless of what was printed on the invoice)
        # Note: _to_float_safe is defined once, earlier in this function, and reused here.
        _tax_company = doc.company or frappe.defaults.get_user_default("company") or ""

        def _resolve_tax_account(description):
            desc = (description or "").lower()
            if "igst" in desc:
                kw = "igst"
            elif "cgst" in desc:
                kw = "cgst"
            elif "sgst" in desc or "utgst" in desc:
                kw = "sgst"
            elif any(k in desc for k in ("gst", "vat", "tax")):
                kw = "gst"
            else:
                return None
            candidates = frappe.get_all(
                "Account",
                filters={"account_name": ["like", f"%{kw}%"], "company": _tax_company, "is_group": 0, "disabled": 0},
                fields=["name", "account_name", "root_type"],
            )
            if not candidates:
                return None
            def _score(c):
                n = (c.account_name or "").lower()
                s = (2 if "input" in n else -3 if "output" in n else 0)
                s += (1 if c.root_type == "Asset" else -1 if c.root_type == "Liability" else 0)
                return s
            candidates.sort(key=_score, reverse=True)
            return candidates[0].name

        extracted_taxes = pi_data.get("taxes", [])
        if isinstance(extracted_taxes, str):
            try:
                extracted_taxes = json.loads(extracted_taxes)
            except Exception:
                extracted_taxes = []
        if not isinstance(extracted_taxes, list):
            extracted_taxes = []

        # If Gemini didn't label individual tax rows (no description/tax_type), infer the
        # standard Indian GST split — a fixed, universal convention, not invoice-specific:
        # exactly two equal/unlabeled rows -> CGST + SGST; exactly one -> IGST.
        _unlabeled_indices = [
            i for i, row in enumerate(extracted_taxes)
            if isinstance(row, dict) and not (row.get("description") or row.get("tax_type"))
        ]
        _inferred_labels = {}
        if len(_unlabeled_indices) == 2:
            _inferred_labels[_unlabeled_indices[0]] = "CGST"
            _inferred_labels[_unlabeled_indices[1]] = "SGST"
        elif len(_unlabeled_indices) == 1:
            _inferred_labels[_unlabeled_indices[0]] = "IGST"

        real_tax_rows, tax_row_failures = [], []
        for idx, row in enumerate(extracted_taxes):
            if not isinstance(row, dict):
                continue
            amount, amt_ok = _to_float_safe(row.get("amount") or row.get("tax_amount"))
            rate, rate_ok = _to_float_safe(row.get("rate"))
            if not amt_ok and not rate_ok:
                tax_row_failures.append(row)
                continue
            if amount <= 0 and rate <= 0:
                continue
            description  = row.get("description") or row.get("tax_type") or _inferred_labels.get(idx) or "Tax"
            account_head = _resolve_tax_account(description)
            if not account_head:
                continue
            # "On Previous Row Total" (not "On Net Total") so a rate-only row's tax also
            # compounds onto any charge rows (Freight/Insurance/etc.) already in the table,
            # not just the item net total — those charges are GST-taxable too under Indian
            # GST rules. Harmless when no charge rows exist (same result as "On Net Total").
            entry = {"charge_type": "Actual" if amount > 0 else "On Previous Row Total",
                      "account_head": account_head, "description": description, "rate": rate}
            if amount > 0:
                entry["tax_amount"] = amount
            real_tax_rows.append(entry)

        if tax_row_failures:
            frappe.log_error(title="AI PI: tax row parse failure", message=str(tax_row_failures))

        tax_fallback_used = False
        if real_tax_rows:
            for row in real_tax_rows:
                doc.append("taxes", row)
        else:
            tax_fallback_used = True
            frappe.log_error(
                title="AI PI: tax fallback used",
                message=(
                    f"No usable extracted tax rows for supplier {pi_data.get('supplier')!r}, "
                    f"bill_no {pi_data.get('bill_no')!r} — falling back to generic tax template. "
                    f"pi_data.taxes={extracted_taxes!r}"
                ),
            )
            default_tax = (
                frappe.db.get_value("Purchase Taxes and Charges Template",
                    {"disabled": 0, "company": _tax_company, "is_default": 1}, "name")
                or frappe.db.get_value("Purchase Taxes and Charges Template",
                    {"disabled": 0, "company": _tax_company}, "name")
            )
            if default_tax:
                doc.taxes_and_charges = default_tax
                try:
                    tax_tmpl = frappe.get_doc("Purchase Taxes and Charges Template", default_tax)
                    for row in (tax_tmpl.taxes or []):
                        # Same charge-on-charges fix as above: "On Net Total" would ignore
                        # any Freight/Insurance charge rows already in the table. Only applied
                        _row_charge_type = (
                            "On Previous Row Total" if row.charge_type == "On Net Total" and (row.rate or 0) > 0
                            else row.charge_type
                        )
                        doc.append("taxes", {
                            "charge_type":  _row_charge_type,
                            "account_head": row.account_head,
                            "description":  row.description,
                            "rate":         row.rate,
                            "cost_center":  getattr(row, "cost_center", None),
                        })
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "AI PI: tax template fallback failed")

        try:
            doc.run_method("calculate_taxes_and_totals")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AI PI: calculate_taxes_and_totals failed")

        # ─── Reconcile extracted grand_total vs ERPNext-recomputed grand_total ───
        from frappe.utils import flt

        GRAND_TOTAL_TOLERANCE = 1.0
        needs_manual_review, review_reason = False, None
        recomputed_grand_total = flt(doc.grand_total)
        extracted_grand_total = None

        try:
            raw_extracted = pi_data.get("grand_total")
            if raw_extracted not in (None, ""):
                extracted_grand_total = flt(raw_extracted)

            if extracted_grand_total is None:
                needs_manual_review = True
                review_reason = (
                    f"No grand_total was found on the source image. ERPNext computed "
                    f"{recomputed_grand_total:.2f} from items/taxes — please verify before submitting."
                )
            else:
                diff = abs(extracted_grand_total - recomputed_grand_total)
                if diff > GRAND_TOTAL_TOLERANCE:
                    needs_manual_review = True
                    review_reason = (
                        f"Extracted grand_total ({extracted_grand_total:.2f}) does not match the "
                        f"recomputed grand_total ({recomputed_grand_total:.2f}); difference of "
                        f"{diff:.2f} exceeds tolerance of {GRAND_TOTAL_TOLERANCE:.2f}. Please verify "
                        f"items/taxes against the source invoice before submitting."
                    )
        except Exception:
            needs_manual_review = True
            review_reason = review_reason or "Could not verify extracted totals against ERPNext computed totals; please check manually."

        if tax_fallback_used and not needs_manual_review:
            needs_manual_review = True
            review_reason = "Could not read tax details from the invoice image — a generic tax template was applied instead. Please verify tax amounts manually."

        doc.flags.ignore_mandatory = True
        doc.flags.ignore_validate  = True
        doc.flags.ignore_links     = True
        doc.insert(ignore_permissions=True)

        if needs_manual_review:
            try:
                doc.add_comment("Comment", review_reason)
            except Exception:
                pass

        site_url = frappe.utils.get_url()
        return {
            "name": doc.name,
            "url": f"{site_url}/app/purchase-invoice/{doc.name}",
            "needs_manual_review": needs_manual_review,
            "review_reason": review_reason,
            "extracted_grand_total": extracted_grand_total,
            "recomputed_grand_total": recomputed_grand_total,
            "tax_fallback_used": tax_fallback_used,
        }

    except Exception as e:
        frappe.throw(_(f"Failed to create Purchase Invoice: {str(e)}"))


# ─── Quotation creation ────────────────────────────────────────────────────────
# Mirrors create_purchase_invoice_from_data above — same item-matching, tax
# handling, and grand-total-reconciliation approach — retargeted to Quotation.
# Two deliberate differences from the PI flow:
#   - No payment_schedule: that's a Sales/Purchase Invoice concept, Quotation
#     doesn't track payment terms since nothing's been billed yet.
#   - Tax account resolution prefers Output/Liability accounts, not
#     Input/Asset — a Quotation is a sales-side document (GST collected FROM
#     the customer), the reverse of a Purchase Invoice (GST paid TO a supplier).

@frappe.whitelist()
def create_quotation_from_data(data):
    import json
    import requests
    from difflib import SequenceMatcher
    from frappe.utils.password import get_decrypted_password

    if not frappe.has_permission("Quotation", "create"):
        frappe.throw(_("You don't have permission to create a Quotation."), frappe.PermissionError)

    current_user    = frappe.session.user
    _mcp_api_key    = frappe.db.get_value("User", current_user, "api_key")
    _mcp_api_secret = None
    if _mcp_api_key:
        try:
            _mcp_api_secret = get_decrypted_password("User", current_user, "api_secret")
        except Exception:
            _mcp_api_secret = None

    def _call_mcp(tool_name, tool_args):
        fac_api_key    = _mcp_api_key
        fac_api_secret = _mcp_api_secret
        try:
            resp = requests.post(
                _mcp_url(),
                headers={"Authorization": f"token {fac_api_key}:{fac_api_secret}", "Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}, "id": 1},
                timeout=30
            )
            if resp.status_code == 200:
                content = resp.json().get("result", {}).get("content", [])
                if content and isinstance(content, list):
                    return content[0].get("text", "")
        except Exception:
            pass
        return ""

    def _similarity(a, b):
        a, b = a.lower().strip(), b.lower().strip()
        r1 = SequenceMatcher(None, a, b).ratio()
        r2 = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
        return max(r1, r2)

    def _to_float_safe(v):
        if isinstance(v, (int, float)):
            return float(v), True
        import re as _re
        s = _re.sub(r"[^\d.\-]", "", str(v or ""))
        if s in ("", "-", "."):
            return 0.0, False
        try:
            return float(s), True
        except Exception:
            return 0.0, False

    try:
        parsed = json.loads(data)
        q_data = parsed.get("mapped_data", parsed)

        items = q_data.get("items", [])
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                items = []

        # Deliberately skip item.get("name") — that field holds row numbers from the
        # quotation table, not the product name (same reasoning as the PI flow).
        items_to_match = []
        for item in items:
            item_name_raw = (
                item.get("item_name") or
                item.get("item_code") or
                item.get("description") or
                ""
            ).strip()
            if item_name_raw:
                items_to_match.append((item_name_raw, item))

        not_found    = []
        matched_rows = []

        if items_to_match:
            mcp_result = _call_mcp("list_documents", {
                "doctype": "Item",
                "filters": {"disabled": 0},
                "fields": ["name", "item_name", "stock_uom"],
                "limit": 1000
            })
            item_master = []
            try:
                parsed_result = json.loads(mcp_result or "[]")
                if isinstance(parsed_result, list):
                    item_master = parsed_result
                elif isinstance(parsed_result, dict):
                    inner = parsed_result.get("result", parsed_result)
                    if isinstance(inner, list):
                        item_master = inner
                    elif isinstance(inner, dict):
                        item_master = inner.get("data", inner.get("message", []))
                    if not item_master:
                        item_master = parsed_result.get("data", parsed_result.get("message", []))
            except Exception:
                pass

            for item_name_raw, item in items_to_match:
                if item_name_raw.isdigit():
                    continue

                best, best_score = None, 0.0
                for candidate in item_master:
                    cname = candidate.get("item_name") or candidate.get("name") or ""
                    score = _similarity(item_name_raw, cname)
                    if score > best_score:
                        best_score = score
                        best = candidate

                if best and best_score >= _FUZZY_MATCH_THRESHOLD:
                    qty = float(item.get("qty", 1)) or 1.0
                    rate = float(item.get("rate", 0))
                    total_amt, total_ok   = _to_float_safe(item.get("total_amount") or item.get("amount"))
                    discount_amt, disc_ok = _to_float_safe(item.get("discount_amount"))
                    if total_ok and total_amt > 0 and qty > 0:
                        if disc_ok and discount_amt > 0:
                            rate = (total_amt - discount_amt) / qty
                        else:
                            rate = total_amt / qty
                    elif disc_ok and discount_amt > 0:
                        rate = max(rate - (discount_amt / qty), 0.0)
                    item_row = {
                        "item_code":   best.get("name", item_name_raw),
                        "item_name":   best.get("item_name", item_name_raw),
                        "description": best.get("item_name", item_name_raw),
                        "qty":         qty,
                        "rate":        rate,
                        "uom":         item.get("uom") or best.get("stock_uom") or "Nos",
                    }
                    skip_item_fields = {"item_code", "item_name", "description", "qty", "rate", "uom", "discount_amount"}
                    for k, v in item.items():
                        if k not in skip_item_fields and v and not isinstance(v, (list, dict)):
                            item_row[k] = v
                    matched_rows.append(item_row)
                else:
                    not_found.append(item_name_raw)

        raw_customer_name = (q_data.get("customer_name") or q_data.get("party_name") or "").strip()
        if raw_customer_name:
            customer_result = _call_mcp("list_documents", {
                "doctype": "Customer",
                "filters": {"disabled": 0},
                "fields": ["name", "customer_name"],
                "limit": 1000
            })
            customer_master = []
            try:
                parsed_customer = json.loads(customer_result or "[]")
                if isinstance(parsed_customer, list):
                    customer_master = parsed_customer
                elif isinstance(parsed_customer, dict):
                    inner = parsed_customer.get("result", parsed_customer)
                    if isinstance(inner, list):
                        customer_master = inner
                    elif isinstance(inner, dict):
                        customer_master = inner.get("data", inner.get("message", []))
                    if not customer_master:
                        customer_master = parsed_customer.get("data", parsed_customer.get("message", []))
            except Exception:
                pass

            best_customer, best_customer_score = None, 0.0
            for candidate in customer_master:
                cname = candidate.get("customer_name") or candidate.get("name") or ""
                score = _similarity(raw_customer_name, cname)
                if score > best_customer_score:
                    best_customer_score = score
                    best_customer = candidate

            if best_customer and best_customer_score >= _FUZZY_MATCH_THRESHOLD:
                q_data["customer_name"] = best_customer.get("customer_name") or best_customer.get("name")
                q_data["party_name"] = best_customer.get("name")
            else:
                not_found.append(f"Customer: {raw_customer_name}")

        if not_found:
            return {
                "status": "items_not_found",
                "not_found": not_found,
                "message": f"These records were not found in ERPNext: {', '.join(not_found)}. Please add them first or correct the name(s)."
            }

        # ─── Match company against the real Company master — NON-BLOCKING. Only
        # override when there's a confident match, for multi-company sites where
        # the quotation's issuing entity/branch differs from the uploader's
        # personal default. If not extracted or not confidently matched, do
        # nothing and let ERPNext's own default-company behavior apply exactly
        # as it already does today.
        raw_company_name = (q_data.get("company") or "").strip()
        if raw_company_name:
            company_result = _call_mcp("list_documents", {
                "doctype": "Company",
                "fields": ["name"],
                "limit": 100
            })
            company_master = []
            try:
                parsed_company = json.loads(company_result or "[]")
                if isinstance(parsed_company, list):
                    company_master = parsed_company
                elif isinstance(parsed_company, dict):
                    inner = parsed_company.get("result", parsed_company)
                    if isinstance(inner, list):
                        company_master = inner
                    elif isinstance(inner, dict):
                        company_master = inner.get("data", inner.get("message", []))
                    if not company_master:
                        company_master = parsed_company.get("data", parsed_company.get("message", []))
            except Exception:
                pass

            best_company, best_company_score = None, 0.0
            for candidate in company_master:
                cname = candidate.get("name") or ""
                score = _similarity(raw_company_name, cname)
                if score > best_company_score:
                    best_company_score = score
                    best_company = candidate

            if best_company and best_company_score >= _FUZZY_MATCH_THRESHOLD:
                q_data["company"] = best_company.get("name")
            else:
                q_data.pop("company", None)

        doc = frappe.new_doc("Quotation")

        skip_fields = {"items", "taxes", "taxes_and_charges",
                       "doctype", "name", "owner", "creation", "modified"}
        _non_data_fieldtypes = {"Section Break", "Column Break", "Tab Break", "Fold",
                                 "HTML", "Button", "Heading", "Table", "Table MultiSelect"}
        _valid_fieldnames = {df.fieldname for df in doc.meta.fields if df.fieldtype not in _non_data_fieldtypes}
        _unmapped_fields, _failed_fields = [], []
        for fieldname, value in q_data.items():
            if fieldname not in skip_fields and value:
                if isinstance(value, (list, dict)):
                    continue
                if fieldname not in _valid_fieldnames:
                    _unmapped_fields.append((fieldname, value))
                    continue
                try:
                    setattr(doc, fieldname, value)
                except Exception:
                    _failed_fields.append((fieldname, value))
        if _unmapped_fields or _failed_fields:
            frappe.log_error(
                title="AI Quotation: field(s) dropped",
                message=(
                    f"Not a real Quotation field (would otherwise be silently dropped by "
                    f"Frappe on insert with no trace): {_unmapped_fields}\n"
                    f"setattr raised an exception: {_failed_fields}"
                ),
            )

        for item_row in matched_rows:
            doc.append("items", item_row)

        if not doc.items:
            doc.append("items", {"item_name": "Item from quotation image", "description": "Please update item details", "qty": 1, "rate": 0, "uom": "Nos"})

        # ─── Apply the ACTUAL tax rows Gemini extracted from the quotation image ───
        _tax_company = doc.company or frappe.defaults.get_user_default("company") or ""

        def _resolve_tax_account(description):
            desc = (description or "").lower()
            if "igst" in desc:
                kw = "igst"
            elif "cgst" in desc:
                kw = "cgst"
            elif "sgst" in desc or "utgst" in desc:
                kw = "sgst"
            elif any(k in desc for k in ("gst", "vat", "tax")):
                kw = "gst"
            else:
                return None
            candidates = frappe.get_all(
                "Account",
                filters={"account_name": ["like", f"%{kw}%"], "company": _tax_company, "is_group": 0, "disabled": 0},
                fields=["name", "account_name", "root_type"],
            )
            if not candidates:
                return None
            def _score(c):
                n = (c.account_name or "").lower()
                # Reversed vs. the PI flow: a Quotation is a sales-side document —
                # GST here is collected FROM the customer (Output tax, a
                # Liability), not paid to a supplier (Input tax, an Asset).
                s = (2 if "output" in n else -3 if "input" in n else 0)
                s += (1 if c.root_type == "Liability" else -1 if c.root_type == "Asset" else 0)
                return s
            candidates.sort(key=_score, reverse=True)
            return candidates[0].name

        extracted_taxes = q_data.get("taxes", [])
        if isinstance(extracted_taxes, str):
            try:
                extracted_taxes = json.loads(extracted_taxes)
            except Exception:
                extracted_taxes = []
        if not isinstance(extracted_taxes, list):
            extracted_taxes = []

        _unlabeled_indices = [
            i for i, row in enumerate(extracted_taxes)
            if isinstance(row, dict) and not (row.get("description") or row.get("tax_type"))
        ]
        _inferred_labels = {}
        if len(_unlabeled_indices) == 2:
            _inferred_labels[_unlabeled_indices[0]] = "CGST"
            _inferred_labels[_unlabeled_indices[1]] = "SGST"
        elif len(_unlabeled_indices) == 1:
            _inferred_labels[_unlabeled_indices[0]] = "IGST"

        real_tax_rows, tax_row_failures = [], []
        for idx, row in enumerate(extracted_taxes):
            if not isinstance(row, dict):
                continue
            amount, amt_ok = _to_float_safe(row.get("amount") or row.get("tax_amount"))
            rate, rate_ok = _to_float_safe(row.get("rate"))
            if not amt_ok and not rate_ok:
                tax_row_failures.append(row)
                continue
            if amount <= 0 and rate <= 0:
                continue
            description  = row.get("description") or row.get("tax_type") or _inferred_labels.get(idx) or "Tax"
            account_head = _resolve_tax_account(description)
            if not account_head:
                continue
            entry = {"charge_type": "Actual" if amount > 0 else "On Previous Row Total",
                      "account_head": account_head, "description": description, "rate": rate}
            if amount > 0:
                entry["tax_amount"] = amount
            real_tax_rows.append(entry)

        if tax_row_failures:
            frappe.log_error(title="AI Quotation: tax row parse failure", message=str(tax_row_failures))

        tax_fallback_used = False
        if real_tax_rows:
            for row in real_tax_rows:
                doc.append("taxes", row)
        else:
            tax_fallback_used = True
            frappe.log_error(
                title="AI Quotation: tax fallback used",
                message=(
                    f"No usable extracted tax rows for customer {q_data.get('customer_name') or q_data.get('party_name')!r} "
                    f"— falling back to generic tax template. q_data.taxes={extracted_taxes!r}"
                ),
            )
            default_tax = (
                frappe.db.get_value("Sales Taxes and Charges Template",
                    {"disabled": 0, "company": _tax_company, "is_default": 1}, "name")
                or frappe.db.get_value("Sales Taxes and Charges Template",
                    {"disabled": 0, "company": _tax_company}, "name")
            )
            if default_tax:
                doc.taxes_and_charges = default_tax
                try:
                    tax_tmpl = frappe.get_doc("Sales Taxes and Charges Template", default_tax)
                    for row in (tax_tmpl.taxes or []):
                        _row_charge_type = (
                            "On Previous Row Total" if row.charge_type == "On Net Total" and (row.rate or 0) > 0
                            else row.charge_type
                        )
                        doc.append("taxes", {
                            "charge_type":  _row_charge_type,
                            "account_head": row.account_head,
                            "description":  row.description,
                            "rate":         row.rate,
                            "cost_center":  getattr(row, "cost_center", None),
                        })
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "AI Quotation: tax template fallback failed")

        try:
            doc.run_method("calculate_taxes_and_totals")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AI Quotation: calculate_taxes_and_totals failed")

        # ─── Reconcile extracted grand_total vs ERPNext-recomputed grand_total ───
        from frappe.utils import flt

        GRAND_TOTAL_TOLERANCE = 1.0
        needs_manual_review, review_reason = False, None
        recomputed_grand_total = flt(doc.grand_total)
        extracted_grand_total = None

        try:
            raw_extracted = q_data.get("grand_total")
            if raw_extracted not in (None, ""):
                extracted_grand_total = flt(raw_extracted)

            if extracted_grand_total is None:
                needs_manual_review = True
                review_reason = (
                    f"No grand_total was found on the source image. ERPNext computed "
                    f"{recomputed_grand_total:.2f} from items/taxes — please verify before submitting."
                )
            else:
                diff = abs(extracted_grand_total - recomputed_grand_total)
                if diff > GRAND_TOTAL_TOLERANCE:
                    needs_manual_review = True
                    review_reason = (
                        f"Extracted grand_total ({extracted_grand_total:.2f}) does not match the "
                        f"recomputed grand_total ({recomputed_grand_total:.2f}); difference of "
                        f"{diff:.2f} exceeds tolerance of {GRAND_TOTAL_TOLERANCE:.2f}. Please verify "
                        f"items/taxes against the source quotation before submitting."
                    )
        except Exception:
            needs_manual_review = True
            review_reason = review_reason or "Could not verify extracted totals against ERPNext computed totals; please check manually."

        if tax_fallback_used and not needs_manual_review:
            needs_manual_review = True
            review_reason = "Could not read tax details from the quotation image — a generic tax template was applied instead. Please verify tax amounts manually."

        doc.flags.ignore_mandatory = True
        doc.flags.ignore_validate  = True
        doc.flags.ignore_links     = True
        doc.insert(ignore_permissions=True)

        if needs_manual_review:
            try:
                doc.add_comment("Comment", review_reason)
            except Exception:
                pass

        site_url = frappe.utils.get_url()
        return {
            "name": doc.name,
            "url": f"{site_url}/app/quotation/{doc.name}",
            "needs_manual_review": needs_manual_review,
            "review_reason": review_reason,
            "extracted_grand_total": extracted_grand_total,
            "recomputed_grand_total": recomputed_grand_total,
            "tax_fallback_used": tax_fallback_used,
        }

    except Exception as e:
        frappe.throw(_(f"Failed to create Quotation: {str(e)}"))


# ─── Speech to Text (Sarvam AI) ───────────────────────────────────────────────

@frappe.whitelist()
def speech_to_text(audio_data):
    import base64
    import requests

    sarvam_api_key = _ai_conf("sarvam_api_key")
    if not sarvam_api_key:
        frappe.throw(_("Sarvam API key not configured in site_config.json"))

    audio_bytes = base64.b64decode(audio_data)

    # Step 1: Speech to Text (auto-detect language)
    stt_resp = requests.post(
        "https://api.sarvam.ai/speech-to-text",
        headers={"api-subscription-key": sarvam_api_key},
        files={"file": ("audio.webm", audio_bytes, "audio/webm")},
        data={"language_code": "unknown"},
        timeout=30
    )

    if stt_resp.status_code != 200:
        frappe.throw(_(f"Speech to text failed: {stt_resp.text}"))

    stt_result  = stt_resp.json()
    transcript  = stt_result.get("transcript", "")
    detected_lang = stt_result.get("language_code", "en-IN")

    # Step 2: Translate to English if regional language detected
    if transcript and detected_lang and not detected_lang.startswith("en"):
        try:
            translate_resp = requests.post(
                "https://api.sarvam.ai/translate",
                headers={"api-subscription-key": sarvam_api_key, "Content-Type": "application/json"},
                json={
                    "input": transcript,
                    "source_language_code": detected_lang,
                    "target_language_code": "en-IN",
                    "speaker_gender": "Male",
                    "mode": "formal",
                    "model": "mayura:v1",
                    "enable_preprocessing": False
                },
                timeout=30
            )
            if translate_resp.status_code == 200:
                translated = translate_resp.json().get("translated_text", "")
                if translated:
                    transcript = translated
        except Exception:
            pass

    return transcript
