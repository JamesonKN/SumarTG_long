"""
Telegram Bot pentru REZUMATE LUNGI de articole
Forwardezi un link sau text → Primești rezumat în română (850-900 caractere)
"""

import os
import re
import logging
import asyncio
from urllib.parse import urlparse
from telegram import Update, MessageEntity
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from telegram.constants import ParseMode
import anthropic
import trafilatura

# Configurare logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Chei API din variabile de mediu
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Inițializare client Anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Prompt pentru articole cu URL - REZUMAT LUNG
SUMMARY_PROMPT_WITH_URL = """Ești un editor de știri experimentat. Primești un articol și trebuie să creezi un REZUMAT DETALIAT în ROMÂNĂ.

REGULI STRICTE:
1. Rezumatul trebuie să aibă EXACT 850-900 de caractere (nu cuvinte, caractere cu spații!)
2. Începe cu un singur emoji relevant pentru subiect (politică=🏛️, economie=💰, tehnologie=💻, război/conflict=⚔️, UE=🇪🇺, Moldova=🇲🇩, România=🇷🇴, Rusia=🇷🇺, SUA=🇺🇸, sport=⚽, sănătate=🏥, mediu=🌍, justiție=⚖️, educație=📚, cultură=🎭, etc.)
3. NU pune bold, italic sau alte formatări
4. NU pune link-uri în text, voi adăuga eu după
5. Scrie la persoana a 3-a, stil jurnalistic neutru și profesionist
6. Dacă articolul e în altă limbă, traduci rezumatul în română
7. Marchează UN SINGUR cuvânt cheie cu acolade, exemplu: {{atacat}} - acesta va deveni link
8. Include detalii importante: cine, ce, când, unde, de ce și cum
9. Structurează logic informația: fapt principal → context → detalii → consecințe/reacții

ARTICOL:
{content}

Răspunde DOAR cu rezumatul (emoji + text cu un cuvânt în acolade), nimic altceva."""

# Prompt pentru text fără URL - REZUMAT LUNG
SUMMARY_PROMPT_NO_URL = """Ești un editor de știri experimentat. Primești un text și trebuie să creezi un REZUMAT DETALIAT în ROMÂNĂ.

REGULI STRICTE:
1. Rezumatul trebuie să aibă EXACT 850-900 de caractere (nu cuvinte, caractere cu spații!)
2. Începe cu un singur emoji relevant pentru subiect (politică=🏛️, economie=💰, tehnologie=💻, război/conflict=⚔️, UE=🇪🇺, Moldova=🇲🇩, România=🇷🇴, Rusia=🇷🇺, SUA=🇺🇸, sport=⚽, sănătate=🏥, mediu=🌍, justiție=⚖️, educație=📚, cultură=🎭, etc.)
3. NU pune bold, italic, link-uri sau alte formatări
4. Scrie la persoana a 3-a, stil jurnalistic neutru și profesionist
5. Dacă textul e în altă limbă, traduci rezumatul în română
6. Include detalii importante: cine, ce, când, unde, de ce și cum
7. Structurează logic informația: fapt principal → context → detalii → consecințe/reacții

TEXT:
{content}

Răspunde DOAR cu rezumatul (emoji + text), nimic altceva."""


def clean_telegram_footer(text: str) -> str:
    """Curăță footerele de Telegram (subscribe links, promo, etc.)."""
    
    footer_patterns = [
        r'Подписаться на .*$',
        r'Подпишись на .*$',
        r'Подписывайтесь.*$',
        r'Прислать контент.*$',
        r'Наш канал.*$',
        r'Читать далее.*$',
        r'Источник.*$',
        r'Subscribe to .*$',
        r'Follow us.*$',
        r'Join our.*$',
        r'Send content.*$',
        r'Abonează-te la .*$',
        r'Urmărește-ne.*$',
        r'Canalul nostru.*$',
        r'\s*\|\s*$',
    ]
    
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        is_footer = False
        
        for pattern in footer_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                is_footer = True
                break
        
        if re.match(r'^\s*https?://t\.me/\S*\s*$', line):
            is_footer = True
        
        if re.match(r'^[\s|/]*https?://[^\s]+[\s|/]*$', line):
            stripped = line.strip()
            if stripped.startswith('http') and stripped.count(' ') == 0:
                if any(x in stripped.lower() for x in ['t.me', 'telegram', 'subscribe', 'join']):
                    is_footer = True
        
        if not is_footer:
            cleaned_lines.append(line)
    
    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()
    
    return '\n'.join(cleaned_lines)


def extract_urls_from_entities(message) -> list:
    """Extrage URL-uri din entities (link-uri pe cuvinte) și din text."""
    urls = []
    
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    
    for entity in entities:
        if entity.type == MessageEntity.URL:
            url = text[entity.offset:entity.offset + entity.length]
            urls.append(url)
        elif entity.type == MessageEntity.TEXT_LINK:
            urls.append(entity.url)
    
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    text_urls = re.findall(url_pattern, text)
    urls.extend(text_urls)
    
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    
    return unique_urls


def format_summary_html(summary: str, url: str = None) -> str:
    """Formatează rezumatul cu HTML: primele 4 cuvinte bold + link pe cuvântul marcat."""
    
    if not summary:
        return summary
    
    emoji = ""
    text = summary
    if summary and not summary[0].isalnum():
        first_space = summary.find(' ')
        if first_space > 0 and first_space <= 4:
            emoji = summary[:first_space]
            text = summary[first_space+1:]
        elif len(summary) > 0:
            for i, char in enumerate(summary):
                if char.isalnum() or char.isspace():
                    emoji = summary[:i]
                    text = summary[i:].lstrip()
                    break
    
    marked_match = re.search(r'\{\{(.+?)\}\}', text)
    marked_word = None
    if marked_match:
        marked_word = marked_match.group(1)
        text = text.replace(f'{{{{{marked_word}}}}}', f'MARKED_PLACEHOLDER_{marked_word}_END')
    
    words = text.split()
    if len(words) >= 4:
        bold_part = ' '.join(words[:4])
        rest_part = ' '.join(words[4:])
        text = f"<b>{bold_part}</b> {rest_part}"
    elif words:
        text = f"<b>{text}</b>"
    
    if marked_word and url:
        placeholder = f'MARKED_PLACEHOLDER_{marked_word}_END'
        link_html = f'<a href="{url}">{marked_word}</a>'
        text = text.replace(placeholder, link_html)
    elif marked_word:
        placeholder = f'MARKED_PLACEHOLDER_{marked_word}_END'
        text = text.replace(placeholder, marked_word)
    
    if emoji:
        return f"{emoji} {text}"
    return text


def is_valid_article_url(url: str) -> bool:
    """Verifică dacă URL-ul e valid pentru extragere articol."""
    if not url:
        return False
    
    skip_domains = [
        't.me', 'telegram.me', 'telegram.org',
        'twitter.com', 'x.com',
        'facebook.com', 'fb.com', 'fb.watch',
        'instagram.com',
        'tiktok.com',
        'youtube.com', 'youtu.be',
        'linkedin.com',
        'wa.me', 'whatsapp.com',
    ]
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        
        for skip in skip_domains:
            if skip in domain:
                return False
        
        return True
    except:
        return False


def extract_article_content(url: str) -> str:
    """Extrage conținutul articolului folosind trafilatura."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            content = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                no_fallback=False
            )
            return content
    except Exception as e:
        logger.error(f"Eroare extragere articol: {e}")
    return None


def get_summary(content: str, has_url: bool = False) -> str:
    """Generează rezumat folosind Claude."""
    try:
        prompt = SUMMARY_PROMPT_WITH_URL if has_url else SUMMARY_PROMPT_NO_URL
        prompt = prompt.format(content=content[:12000])
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        return message.content[0].text.strip()
    except Exception as e:
        logger.error(f"Eroare Claude API: {e}")
        return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler pentru /start."""
    await update.message.reply_text(
        "👋 Salut! Sunt botul pentru REZUMATE DETALIATE.\n\n"
        "📝 Trimite-mi:\n"
        "• Un link către un articol\n"
        "• Un text (forward sau copiat)\n"
        "• Un mesaj forward din Telegram\n\n"
        "📏 Îți returnez un rezumat detaliat de ~850-900 caractere în română, "
        "cu emoji relevant și primele 4 cuvinte bold.\n\n"
        "💡 Ideal pentru articole mai complexe care necesită mai mult context!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal pentru mesaje."""
    
    message = update.message
    if not message:
        return
    
    text = message.text or message.caption or ""
    
    if not text.strip():
        await message.reply_text("❌ Trimite-mi un text sau un link.")
        return
    
    processing_msg = await message.reply_text("⏳ Procesez...")
    
    urls = extract_urls_from_entities(message)
    article_urls = [u for u in urls if is_valid_article_url(u)]
    
    content = None
    source_url = None
    
    if article_urls:
        source_url = article_urls[0]
        logger.info(f"Extrag articol de la: {source_url}")
        content = extract_article_content(source_url)
    
    if content:
        summary = get_summary(content, has_url=True)
    else:
        cleaned_text = clean_telegram_footer(text)
        
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        cleaned_text = re.sub(url_pattern, '', cleaned_text)
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        
        if len(cleaned_text) < 50:
            await processing_msg.edit_text(
                "❌ Textul e prea scurt sau nu am putut extrage conținut."
            )
            return
        
        summary = get_summary(cleaned_text, has_url=bool(source_url))
    
    if not summary:
        await processing_msg.edit_text(
            "❌ Nu am putut genera rezumatul. Încearcă din nou."
        )
        return
    
    formatted_summary = format_summary_html(summary, source_url)
    
    char_count = len(summary.replace('{{', '').replace('}}', ''))
    formatted_summary += f"\n\n📊 <i>{char_count} caractere</i>"
    
    await processing_msg.edit_text(formatted_summary, parse_mode=ParseMode.HTML)


async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler pentru mesaje forwardate."""
    await handle_message(update, context)


def main():
    """Pornește botul."""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN nu e setat!")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY nu e setat!")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))
    application.add_handler(MessageHandler(
        filters.FORWARDED,
        handle_forwarded
    ))
    
    logger.info("Botul pentru rezumate lungi pornește...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
