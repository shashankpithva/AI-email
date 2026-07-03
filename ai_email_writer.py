#!/usr/bin/env python3
"""
AI Email Automation (free LLMs) + Resend bulk sending
-----------------------------------------------------
- YOU design the email template (HTML + plain text) in code.
- A FREE LLM writes the subject line and the body content.
- Send to ONE recipient (SMTP) OR to your whole Resend audience (Resend API).

The LLM part uses the OpenAI-compatible endpoints every provider exposes, so the
same `openai` client works for all of them -- only the base URL, model, and key
change.

==============================================================================
THREE WAYS TO USE IT
==============================================================================
1. Preview (no sending):
     python3 ai_email_writer.py --goal "..." --to alex@example.com

2. Send to ONE person via SMTP (needs SMTP_* in .env):
     python3 ai_email_writer.py --goal "..." --to alex@example.com --send

3. Send to your ENTIRE Resend audience (needs RESEND_* in .env):
     python3 ai_email_writer.py --goal "..." --resend
     python3 ai_email_writer.py --goal "..." --resend --dry-run   # preview list

------------------------------------------------------------------------------
FREE LLM PROVIDERS (pick one with --provider)
------------------------------------------------------------------------------
  groq        https://console.groq.com/keys      (fast, recommended)
  gemini      https://aistudio.google.com/apikey
  openrouter  https://openrouter.ai/keys
  ollama      https://ollama.com                 (100% local, no key)

------------------------------------------------------------------------------
ENV VARS (put in a .env or .env.local file next to this script)
------------------------------------------------------------------------------
  # LLM (one of these, matching --provider)
  GROQ_API_KEY=gsk_...

  # Resend bulk sending
  RESEND_API_KEY=re_...
  RESEND_FROM=OneMan <hello@yourdomain.com>   # must be a verified domain
  RESEND_AUDIENCE_ID=xxxxxxxx-xxxx-...         # optional (see --audience-id)

  # SMTP single-send (only for --send)
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=you@gmail.com
  SMTP_PASS=your-app-password

INSTALL:
    pip install openai
"""

import os
import re
import json
import argparse
import smtplib
import urllib.request
import urllib.error
from pathlib import Path
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# ---------------------------------------------------------------------------
# .env LOADER  (tiny, no external dependency)
# ---------------------------------------------------------------------------
def load_env_file(path):
    """Load KEY=VALUE lines from a single .env file into os.environ."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_dotenvs():
    """Load .env.local (highest priority) then .env, next to this script."""
    here = Path(__file__).resolve().parent
    load_env_file(here / ".env.local")
    load_env_file(here / ".env")


# ---------------------------------------------------------------------------
# FREE PROVIDER PRESETS
# ---------------------------------------------------------------------------
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
        "key_env": None,
    },
}

RESEND_BASE = "https://api.resend.com"
BATCH_LIMIT = 100  # Resend allows up to 100 emails per batch call


@dataclass
class EmailBrief:
    goal: str
    recipient_name: str
    sender_name: str
    tone: str = "friendly, confident, concise"
    extra_context: str = ""


# ---------------------------------------------------------------------------
# LLM: generate subject + body content (returns structured JSON)
# ---------------------------------------------------------------------------
def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
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

    if cfg["key_env"]:
        api_key = os.environ.get(cfg["key_env"])
        if not api_key:
            raise SystemExit(
                f"Missing API key '{cfg['key_env']}'.\n"
                f"Add it to a .env or .env.local file:  {cfg['key_env']}=your-key-here"
            )
    else:
        api_key = "ollama"

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

    try:
        response = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_object"}, temperature=0.7,
        )
    except Exception:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0.7,
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
            <tr>
              <td style="background:#111827;padding:24px 32px;">
                <span style="color:#ffffff;font-size:18px;font-weight:600;">OneMan</span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">
                <p style="margin:0 0 16px;font-size:16px;font-weight:600;color:#111;">
                  {content['greeting']}
                </p>
                {body_paragraphs}
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
    lines = [content["greeting"], ""]
    lines += content["paragraphs"]
    lines += ["", content["cta"], "", f"Best,\n{sender_name}"]
    return "\n".join(lines)


def personalize(content: dict, first_name: str) -> dict:
    """Return a copy of content with a greeting personalized to first_name."""
    copy = dict(content)
    if first_name:
        copy["greeting"] = f"Hi {first_name},"
    return copy


# ---------------------------------------------------------------------------
# SEND: single email via SMTP
# ---------------------------------------------------------------------------
def send_email_smtp(to_address: str, subject: str, html: str, text: str):
    for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"):
        if not os.environ.get(var):
            raise SystemExit(f"Cannot send: missing {var}. Add SMTP_* to your .env file.")

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
# RESEND: HTTP helpers + audience fetch + bulk batch send
# ---------------------------------------------------------------------------
def _resend_request(method: str, path: str, api_key: str, payload=None):
    url = f"{RESEND_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[Resend error {e.code}] {method} {path}\n{detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"[Network error] could not reach Resend: {e.reason}")


def resend_list_contacts(api_key: str, audience_id: str = None):
    """Fetch contacts. Uses the audience-scoped endpoint when an audience id is
    given, otherwise the global contacts endpoint."""
    path = f"/audiences/{audience_id}/contacts" if audience_id else "/contacts"
    result = _resend_request("GET", path, api_key)
    return result.get("data", [])


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def resend_send_batch(api_key, from_addr, messages):
    """messages: list of dicts {to, subject, html, text}. Sends in <=100 chunks."""
    sent = 0
    for batch in _chunks(messages, BATCH_LIMIT):
        payload = [
            {
                "from": from_addr,
                "to": [m["to"]],
                "subject": m["subject"],
                "html": m["html"],
                "text": m["text"],
            }
            for m in batch
        ]
        result = _resend_request("POST", "/emails/batch", api_key, payload)
        sent += len(result.get("data", []))
        print(f"[Resend] sent batch of {len(batch)} (total {sent})")
    return sent


def run_resend(brief, content, sender_name, args):
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise SystemExit("Missing RESEND_API_KEY. Add it to your .env file (re_...).")

    from_addr = args.from_addr or os.environ.get("RESEND_FROM")
    if not from_addr:
        raise SystemExit(
            "Missing sender. Set RESEND_FROM in .env or pass --from "
            '"Name <hello@yourdomain.com>". The domain must be verified in Resend '
            "(or use onboarding@resend.dev for testing)."
        )

    audience_id = args.audience_id or os.environ.get("RESEND_AUDIENCE_ID")
    contacts = resend_list_contacts(api_key, audience_id)

    # Keep only subscribed contacts that have an email.
    recipients = [c for c in contacts if c.get("email") and not c.get("unsubscribed")]
    skipped = len(contacts) - len(recipients)

    print(f"\n[Resend] {len(contacts)} contacts found, "
          f"{len(recipients)} subscribed, {skipped} skipped (unsubscribed/no email).")

    if not recipients:
        raise SystemExit("No subscribed recipients to send to.")

    # Build one message per recipient (identical mail, optional personalized greeting).
    messages = []
    for c in recipients:
        c_content = personalize(content, c.get("first_name")) if args.personalize else content
        messages.append({
            "to": c["email"],
            "subject": content["subject"],
            "html": render_html(c_content, sender_name),
            "text": render_plaintext(c_content, sender_name),
        })

    if args.dry_run:
        print("\n[DRY RUN] Would send to:")
        for m in messages:
            print(f"  - {m['to']}")
        print(f"\n[DRY RUN] {len(messages)} emails NOT sent. Remove --dry-run to send.")
        return

    total = resend_send_batch(api_key, from_addr, messages)
    print(f"\n[OK] Sent to {total} recipients via Resend.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate an email with a FREE LLM; preview, SMTP-send, or Resend bulk-send."
    )
    parser.add_argument("--provider", default="groq", choices=list(PROVIDERS),
                        help="Which free LLM provider to use (default: groq).")
    parser.add_argument("--model", default=None, help="Override the provider's default model.")
    parser.add_argument("--goal", required=True, help="What the email should accomplish.")
    parser.add_argument("--to", default=None, help="Single recipient (preview/SMTP mode).")
    parser.add_argument("--recipient-name", default="there", help="Recipient's name.")
    parser.add_argument("--sender-name", default="Me", help="Your name (for the signature).")
    parser.add_argument("--tone", default="friendly, confident, concise", help="Desired tone.")
    parser.add_argument("--context", default="", help="Extra facts the LLM should use.")
    parser.add_argument("--send", action="store_true", help="Send to --to via SMTP.")
    parser.add_argument("--save-html", metavar="PATH", help="Save the rendered HTML to a file.")

    # Resend bulk options
    parser.add_argument("--resend", action="store_true",
                        help="Send to your entire Resend audience.")
    parser.add_argument("--audience-id", default=None,
                        help="Resend audience id (else RESEND_AUDIENCE_ID, else all contacts).")
    parser.add_argument("--from", dest="from_addr", default=None,
                        help='Sender, e.g. "OneMan <hello@yourdomain.com>" (else RESEND_FROM).')
    parser.add_argument("--personalize", action="store_true",
                        help="Personalize the greeting with each contact's first name.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --resend: list recipients without sending.")
    return parser.parse_args()


def main():
    load_dotenvs()
    args = parse_args()

    if not args.resend and not args.to:
        raise SystemExit("Provide --to <email> (single) or --resend (whole audience).")

    # In bulk mode, default to a generic greeting so it reads well for everyone.
    brief = EmailBrief(
        goal=args.goal,
        recipient_name=args.recipient_name,
        sender_name=args.sender_name,
        tone=args.tone,
        extra_context=args.context,
    )

    print(f"Generating email content with '{args.provider}'...")
    content = generate_email_content(brief, args.provider, args.model)
    html = render_html(content, brief.sender_name)
    text = render_plaintext(content, brief.sender_name)

    # Preview
    print("\n" + "=" * 60)
    print("SUBJECT:", content["subject"])
    print("=" * 60)
    print(text)
    print("=" * 60)

    if args.save_html:
        Path(args.save_html).write_text(html, encoding="utf-8")
        print(f"\n[OK] HTML saved to {args.save_html}")

    if args.resend:
        run_resend(brief, content, brief.sender_name, args)
    elif args.send:
        send_email_smtp(args.to, content["subject"], html, text)
    else:
        print("\n(Preview only. Use --send for SMTP, or --resend for your audience.)")


if __name__ == "__main__":
    main()
