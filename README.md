# AI Email Writer

An AI automation that writes an email using a **free LLM**. You design the email
template in code; the LLM writes the subject and body content.

## Features
- Template lives entirely in `render_html()` -- you control the design.
- LLM writes only the subject + body (returned as structured JSON).
- Works with multiple **free** providers: Groq, Google Gemini, OpenRouter, and
  local Ollama (no API key).
- Optional SMTP sending.

## Setup

```bash
# 1. Install the one dependency
pip install openai

# 2. Create your local secrets file from the template
cp .env.example .env
# then open .env and fill in ONE provider key (e.g. GROQ_API_KEY)
```

Get a free key:
- Groq: https://console.groq.com/keys
- Gemini: https://aistudio.google.com/apikey
- OpenRouter: https://openrouter.ai/keys
- Ollama (no key, local): https://ollama.com  then `ollama pull llama3.2`

## Run (preview only -- does not send)

```bash
python3 ai_email_writer.py \
  --provider groq \
  --goal "Invite the recipient to join the OneMan private beta" \
  --to alex@example.com \
  --recipient-name Alex \
  --sender-name Shashank
```

## Actually send it

Fill in the SMTP_* values in `.env` (Gmail needs an App Password), then add `--send`:

```bash
python3 ai_email_writer.py --provider groq --goal "..." --to alex@example.com --send
```

## Options

| Flag | Description |
|------|-------------|
| `--provider` | `groq` (default), `gemini`, `openrouter`, `ollama` |
| `--model` | Override the default model for the provider |
| `--goal` | What the email should accomplish (required) |
| `--to` | Recipient email address (required) |
| `--recipient-name` | Recipient's name |
| `--sender-name` | Your name (signature) |
| `--tone` | Desired tone |
| `--context` | Extra facts for the LLM |
| `--send` | Actually send via SMTP |
| `--save-html PATH` | Save rendered HTML to a file |

## Security
- `.env` is git-ignored -- your keys never get committed.
- Only `.env.example` (no secrets) is tracked.
