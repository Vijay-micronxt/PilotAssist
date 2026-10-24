# PilotAssist

AI chat assistant for ERPNext. Install it on any ERPNext site and a chat button appears on every desk page. Users can ask questions about their ERP data, upload invoice or quotation images to create documents, and use voice input.

## Requirements

- Frappe / ERPNext v15
- **frappe_assistant_core** installed on the same site. The assistant reads all ERP data through its MCP endpoint (`/api/method/frappe_assistant_core.api.fac_endpoint.handle_mcp`). To use an MCP server on another site instead, set `mcp_base_url` (see Configuration).
- An API key for at least one AI provider (Gemini by default).
- Optional, for OCR of uploaded photos: the system packages `tesseract-ocr` and `ghostscript` (e.g. `sudo apt install tesseract-ocr ghostscript`). Without them, photos are sent to the AI as-is.

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO
bench --site <your-site> install-app pilot_assist
bench build --app pilot_assist
bench restart
```

`bench get-app` installs the Python dependencies (google-genai, anthropic, boto3, img2pdf, ocrmypdf).

Installing the app adds an **Enable Assistant Access** checkbox to the User form (ticked by default). Untick it to block a user from the assistant. If the site already has this field (frappe_assistant_core can create it), it is left unchanged.

## Configuration

All settings go in the site's `site_config.json`, or with `bench --site <your-site> set-config`.

### Main block

```json
"enable_ai_assistant": {
  "enable_ai_chat": 1,
  "ai_provider": "gemini",
  "gemini_api_keys": ["<key 1>", "<key 2>"],
  "gemini_model": "gemini-flash-lite-latest",
  "enable_speech_to_text": 0,
  "sarvam_api_key": "<key>",
  "enable_role_based_ai": 0,
  "role_based_ai_credentials": [],
  "mcp_base_url": "https://other-site.example.com"
}
```

| Key | Purpose |
| --- | --- |
| `enable_ai_chat` | `1` shows the chat button. `0` hides it. |
| `ai_provider` | `gemini` (default), `claude`, `openrouter`, `kimi`, `glm`, `deepseek` or `bedrock`. |
| `gemini_api_keys` | One or more Gemini keys. When one hits its rate limit, the next is used. |
| `gemini_model` | Gemini model. Default `gemini-flash-lite-latest`. |
| `enable_speech_to_text` | `1` shows the microphone button. Needs `sarvam_api_key`. |
| `sarvam_api_key` | Sarvam AI key for speech-to-text and translation to English. |
| `enable_role_based_ai` | `1` turns on role-based access (below). |
| `role_based_ai_credentials` | List of `{"role": "<ERPNext Role>", "token": "<api_key>:<api_secret>"}`. |
| `mcp_base_url` | Optional. MCP server base URL. Defaults to this site's own URL. |

### Other providers (top-level keys, not inside the block)

| Provider | Keys |
| --- | --- |
| Claude | `claude_api_key`, `claude_model` |
| OpenRouter | `openrouter_api_key`, `openrouter_model` |
| Kimi | `kimi_api_key`, `kimi_model` |
| GLM | `glm_api_key`, `glm_model` |
| DeepSeek | `deepseek_api_key`, `deepseek_model` (default `deepseek-chat`) |
| AWS Bedrock | `bedrock_access_key`, `bedrock_secret_key`, `bedrock_region`, `bedrock_model` |

Example:

```bash
bench --site <your-site> set-config enable_ai_assistant '{"enable_ai_chat": 1, "ai_provider": "deepseek"}' --parse
bench --site <your-site> set-config deepseek_api_key "<key>"
```

### Access

- Each user needs **Enable Assistant Access** ticked.
- By default the assistant uses the logged-in user's own API key and secret (User → API Access → Generate Keys), so normal ERPNext permissions apply.
- With role-based mode on, a user whose Role matches an entry in `role_based_ai_credentials` uses that entry's token instead. A user with 2+ matching roles gets a role picker in the chat header. Administrator always uses its own access.

## Features

- Questions in plain English about any ERPNext data, answered through MCP tools (list, get, search, reports, field info).
- Knows the open document: on a form, the record is loaded first, so "summarize this invoice" works.
- Link to the document when an answer is about one record.
- Long lists show 10 rows with a Load More button that fetches more rows without calling the AI again.
- Follow-up questions (the last 6 messages are sent as context).
- Upload an image (resized to 1024px) or a PDF (up to 10 MB) with preview, full-size view and rotate.
- Create a Purchase Invoice or a Quotation from an uploaded image: an editable preview card, item/supplier/customer matching against your masters, GST tax accounts, a default-template fallback, and grand-total reconciliation with a review comment on mismatch.
- Voice input with auto-stop after 3 seconds of silence and translation of regional languages (Sarvam AI).
- Copy and Retry on messages. Resizable panel on desktop, full screen on mobile.

## APIs

All are POST requests to `/api/method/<path>` with `Authorization: token <api_key>:<api_secret>`.

| Path | Purpose |
| --- | --- |
| `pilot_assist.api.ai_api.ask_document` | The chat used by the widget. |
| `pilot_assist.api.ai_api.chat_api` | Chat for external apps. Body: `message`, optional `conversation_history`. Returns structured JSON. |
| `pilot_assist.api.ai_api.extract_fields_from_message` | Maps an arbitrary JSON body to real ERPNext field names. Doctype in the `X-Doctype` header (`Quotation` or `Purchase Invoice`). |
| `pilot_assist.api.ai_api.load_more_list_records` | Next page of a list answer. |
| `pilot_assist.api.ai_api.create_purchase_invoice_from_data` | Creates a Purchase Invoice from mapped data. |
| `pilot_assist.api.ai_api.create_quotation_from_data` | Creates a Quotation from mapped data. |
| `pilot_assist.api.ai_api.speech_to_text` | Audio (base64 webm) in, English text out. |

## Using with erpnext_enhancements

erpnext_enhancements contains its own copy of this assistant. If both apps are installed on one site, only one chat button appears (whichever app's script loads first), and the site config is shared. Prefer installing only one of them per site.

## License

MIT
