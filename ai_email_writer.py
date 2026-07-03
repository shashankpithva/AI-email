#!/usr/bin/env python3
"""
AI Email Automation (free LLMs)
-------------------------------
- YOU design the email template (HTML + plain text) in code.
- A FREE LLM writes the subject line and the body content.
- The generated content is injected into your template, then optionally sent via SMTP.

This uses the OpenAI-compatible endpoints that every supported provider exposes,
so the same `openai` client works for all of them -- only the base URL, model,
and key change.

------------------------------------------------------------------------------
FREE PROVIDER OPTIONS  (pick one with --provider)
------------------------------------------------------------------------------
  groq        Free, very fast. Get a key: https://console.groq.com/keys
              export GROQ_API_KEY="gsk_..."

  gemini      Free, generous limits. Key: https://aistudio.google.com/apikey
              export GEMINI_API_KEY="..."

  openrouter  Free ':free' models. Key: https://openrouter.ai/keys
              export OPENROUTER_API_KEY="sk-or-..."

  ollama      100% local, no key, works offline. Install: https://ollama.com
              Then:  ollama pull llama3.2   (and keep `ollama serve` running)
------------------------------------------------------------------------------

INSTALL:
    pip install openai

EXAMPLE (preview only, using Groq):
    export GROQ_API_KEY="gsk_..."
    python3 ai_email_writer.py \\
        --provider groq \\
        --goal "Invite the recipient to join the OneMan private beta" \\
        --to alex@example.com \\
        --recipient-name Alex \\
        --sender-name Shashank

EXAMPLE (fully local with Ollama, then actually send):
    python3 ai_email_writer.py --provider ollama \\
        --goal "..." --to alex@example.com --send
"""

import os
import re
import json
import argparse
import smtplib
from pathlib import Path
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ---------------------------------------------------------------------------
# .env LOADER  (tiny, no external dependency)
# ---------------------------------------------------------------------------
def load_env_file(path: str = None):
    """Load KEY=VALUE lines from a .env file into os.environ.

    - Looks for a .env next to this script by default.
    - Ignores blank lines and lines starting with '#'.
    - Strips optional surrounding quotes around values.
    - Does NOT overwrite variables you already set with `export`.
    """
    env_path = Path(path) if path else Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # existing environment variables win over the .env file
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# FREE PROVIDER PRESETS
# ---------------------------------------------------------------------------
# Each preset defines: the OpenAI-compatible base URL, a good default model,
# and which environment variable holds the API key (None = no key needed).
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.0-flash",
        "key_env": "GEMINI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "key_env": None,  # local, no key required
    },
}


@dataclass
class EmailBrief:
    """What you want the email to be about."""
    goal: str
    recipient_name: str
    sender_name: str
    tone: str = "friendly, confident, concise"
    extra_context: str = ""


# ---------------------------------------------------------------------------
# LLM: generate subject + body content (returns structured JSON)
# ---------------------------------------------------------------------------
def _extract_json(raw: str) -> dict:
    """Robustly pull a JSON object out of the model's reply.
    Some free models wrap JSON in ```json fences or add stray text."""
    raw = raw.strip()
    # strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # fall back to grabbing the outermost {...}
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def generate_email_content(brief: EmailBrief, provider: str, model: str = None) -> dict:
    from openai import OpenAI

    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Choose from: {list(PROVIDERS)}")

    cfg = PROVIDERS[provider]
    model = model or cfg["model"]

    # Resolve API key (Ollama needs a non-empty placeholder but no real key).
    if cfg["key_env"]:
        api_key = os.environ.get(cfg["key_env"])
        if not api_key:
            raise SystemExit(
                f"Missing API key. Set it with:  export {cfg['key_env']}=\"your-key\""
            )
    else:
        api_key = "ollama"  # dummy value; local server ignores it

    client = OpenAI(api_key=api_key, base_url=cfg["base_url"])

    system_prompt = (
        "You are an expert email copywriter. "
        "Write a subject line and email body that achieve the given goal. "
        "Return ONLY valid JSON (no markdown, no code fences) with keys: "
        "'subject', 'greeting', 'paragraphs' (a list of short strings), "
        "and 'cta' (a short call-to-action). "
        "Do not include the signature -- that is handled by the template."
    )

    user_prompt = f"""
    Goal: {brief.goal}
    Recipient name: {brief.recipient_name}
    Sender name: {brief.sender_name}
    Tone: {brief.tone}
    Extra context: {brief.extra_context or "none"}
    """

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Try native JSON mode first; not all free models support it, so fall back.
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7,
        )
    except Exception:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
        )

    return _extract_json(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# TEMPLATE: YOU design this. LLM content is injected via placeholders.
# ---------------------------------------------------------------------------
def render_html(content: dict, sender_name: str) -> str:
    body_paragraphs = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#333;">{p}</p>'
        for p in content["paragraphs"]
    )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f4f7;font-family:Helvetica,Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
      <tr>
        <td align="center">
          <table width="560" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:12px;overflow:hidden;
                        box-shadow:0 2px 8px rgba(0,0,0,0.06);">
            <!-- Header -->
            <tr>
              <td style="background:#111827;padding:24px 32px;">
                <span style="color:#ffffff;font-size:18px;font-weight:600;">OneMan</span>
              </td>
            </tr>
            <!-- Body -->
            <tr>
              <td style="padding:32px;">
                <p style="margin:0 0 16px;font-size:16px;font-weight:600;color:#111;">
                  {content['greeting']}
                </p>
                {body_paragraphs}
                <!-- CTA button -->
                <table cellpadding="0" cellspacing="0" style="margin:24px 0;">
                  <tr>
                    <td style="background:#111827;border-radius:8px;">
                      <a href="#" style="display:inline-block;padding:12px 24px;
                         color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;">
                        {content['cta']}
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;font-size:15px;color:#333;">
                  Best,<br><strong>{sender_name}</strong>
                </p>
              </td>
            </tr>
            <!-- Footer -->
            <tr>
              <td style="padding:20px 32px;background:#f9fafb;font-size:12px;color:#9ca3af;">
                Sent by the OneMan AI co-founder &middot; <a href="#" style="color:#9ca3af;">Unsubscribe</a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def render_plaintext(content: dict, sender_name: str) -> str:
    """Fallback for clients that don't render HTML."""
    lines = [content["greeting"], ""]
    lines += content["paragraphs"]
    lines += ["", content["cta"], "", f"Best,\n{sender_name}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SEND via SMTP
# ---------------------------------------------------------------------------
def send_email(to_address: str, subject: str, html: str, text: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = to_address
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as server:
        server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(msg)
    print(f"\n[OK] Sent '{subject}' to {to_address}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate an email with a FREE LLM and inject it into your template."
    )
    parser.add_argument("--provider", default="groq", choices=list(PROVIDERS),
                        help="Which free LLM provider to use (default: groq).")
    parser.add_argument("--model", default=None,
                        help="Override the default model for the chosen provider.")
    parser.add_argument("--goal", required=True, help="What the email should accomplish.")
    parser.add_argument("--to", required=True, help="Recipient email address.")
    parser.add_argument("--recipient-name", default="there", help="Recipient's name.")
    parser.add_argument("--sender-name", default="Me", help="Your name (for the signature).")
    parser.add_argument("--tone", default="friendly, confident, concise", help="Desired tone.")
    parser.add_argument("--context", default="", help="Extra facts the LLM should use.")
    parser.add_argument("--send", action="store_true", help="Actually send the email via SMTP.")
    parser.add_argument("--save-html", metavar="PATH", help="Save the rendered HTML to a file.")
    return parser.parse_args()


def main():
    load_env_file()  # pull keys from .env if present (export still overrides)
    args = parse_args()

    brief = EmailBrief(
        goal=args.goal,
        recipient_name=args.recipient_name,
        sender_name=args.sender_name,
        tone=args.tone,
        extra_context=args.context,
    )

    print(f"Generating email content with '{args.provider}'...")
    content = generate_email_content(brief, args.provider, args.model)  # free LLM writes subject + body
    html = render_html(content, brief.sender_name)                      # your template
    text = render_plaintext(content, brief.sender_name)

    # Preview
    print("\n" + "=" * 60)
    print("SUBJECT:", content["subject"])
    print("=" * 60)
    print(text)
    print("=" * 60)

    if args.save_html:
        with open(args.save_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n[OK] HTML saved to {args.save_html}")

    if args.send:
        send_email(args.to, content["subject"], html, text)
    else:
        print("\n(Preview only. Re-run with --send to actually send the email.)")


if __name__ == "__main__":
    main()
