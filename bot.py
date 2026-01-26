"""
Telegram Bot pentru rezumate de articole
Forwardezi un link sau text → Primești rezumat în română (850-950 caractere)
"""

import os
import re
import logging
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

# Verificare la startup
if ANTHROPIC_API_KEY:
    logger.info(f"ANTHROPIC_API_KEY setat (primele 10 char): {ANTHROPIC_API_KEY[:10]}...")
else:
    logger.error("ANTHROPIC_API_KEY NU este setat!")

# Inițializare client Anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Prompt pentru articole cu URL
SUMMARY_PROMPT_WITH_URL = """Ești un editor de știri. Primești un articol și trebuie să creezi un rezumat în ROMÂNĂ.

REGULI STRICTE:
1. Rezumatul trebuie să aibă EXACT 850-950 de caractere (nu cuvinte, caractere!)
2. Împarte rezumatul în 2-3 paragrafe scurte, separate prin linie goală
3. Începe cu un singur emoji relevant pentru subiect (politică=🏛️, economie=💰, tehnologie=💻, război/conflict=⚔️, UE=🇪🇺, Moldova=🇲🇩, România=🇷🇴, Rusia=🇷🇺, SUA=🇺🇸, sport=⚽, sănătate=🏥, mediu=🌍, etc.)
4. NU pune bold, italic sau alte formatări
5. NU pune link-uri în text, voi adăuga eu după
6. Scrie la persoana a 3-a, stil jurnalistic neutru
7. Dacă articolul e în altă limbă, traduci rezumatul în română
8. Marchează UN SINGUR cuvânt cheie cu acolade, exemplu: {{atacat}} - acesta va deveni link

ARTICOL:
{content}

Răspunde DOAR cu rezumatul (emoji + text cu un cuvânt în acolade, în 2-3 paragrafe), nimic altceva."""

# Prompt pentru text fără URL
SUMMARY_PROMPT_NO_URL = """Ești un editor de știri. Primești un text și trebuie să creezi un rezumat în ROMÂNĂ.

REGULI STRICTE:
1. Rezumatul trebuie să aibă EXACT 850-950 de caractere (nu cuvinte, caractere!)
2. Împarte rezumatul în 2-3 paragrafe scurte, separate prin linie goală
3. Începe cu un singur emoji relevant pentru subiect (politică=🏛️, economie=💰, tehnologie=💻, război/conflict=⚔️, UE=🇪🇺, Moldova=🇲🇩, România=🇷🇴, Rusia=🇷🇺, SUA=🇺🇸, sport=⚽, sănătate=🏥, mediu=🌍, etc.)
4. NU pune bold, italic, link-uri sau alte formatări
5. Scrie la persoana a 3-a, stil jurnalistic neutru
6. Dacă textul e în altă limbă, traduci rezumatul în română

TEXT:
{content}

Răspunde DOAR cu rezumatul (emoji + text, în 2-3 paragrafe), nimic altceva."""


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
        
        if re.match(r'^[\s|/]*https?://\S+[\s|/]*$', line):
            is_footer = True
            
        if not is_footer:
            cleaned_lines.append(line)
    
    cleaned_text = '\n'.join(cleaned_lines)
    cleaned_text = re.sub(r'\s*\(https?://t\.me/[^)]+\)', '', cleaned_text)
    cleaned_text = re.sub(r'\s*\(https?://max\.ru/[^)]+\)', '', cleaned_text)
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
    cleaned_text = cleaned_text.strip()
    
    return cleaned_text


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


def filter_article_urls(urls: list) -> list:
    """Filtrează URL-urile, păstrând doar cele către articole."""
    
    ignore_domains = [
        't.me',
        'telegram.me',
        'max.ru',
        'twitter.com',
        'x.com',
        'facebook.com',
        'instagram.com',
        'tiktok.com',
        'youtube.com',
        'youtu.be',
    ]
    
    article_urls = []
    
    for url in urls:
        try:
            domain = urlparse(url).netloc.lower()
            
            is_ignored = False
            for ignore in ignore_domains:
                if ignore in domain:
                    is_ignored = True
                    break
            
            if not is_ignored:
                article_urls.append(url)
                
        except:
            pass
    
    return article_urls


def format_summary_html(summary: str, url: str = None) -> str:
    """Formatează rezumatul cu HTML: primele 4 cuvinte bold + link pe cuvântul marcat."""
    
    summary = summary.replace("**", "").replace("*", "").replace("__", "")
    summary = summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    if len(summary) > 0 and not summary[0].isalnum() and summary[0] not in '([{':
        i = 0
        while i < len(summary):
            if summary[i].isalnum():
                break
            i += 1
        emoji_part = summary[:i].rstrip()
        text_part = summary[i:].lstrip()
    else:
        emoji_part = ""
        text_part = summary
    
    link_word = None
    link_word_match = re.search(r'\{+([^}]+)\}+', text_part)
    if link_word_match:
        link_word = link_word_match.group(1)
        text_part = text_part[:link_word_match.start()] + link_word + text_part[link_word_match.end():]
    
    words = text_part.split()
    result_words = []
    
    for i, word in enumerate(words):
        is_link_word = link_word and link_word in word
        
        if i < 4:
            if is_link_word and url:
                word_with_link = word.replace(link_word, f'<a href="{url}">{link_word}</a>')
                if i == 0:
                    result_words.append(f"<b>{word_with_link}")
                elif i == 3:
                    result_words.append(f"{word_with_link}</b>")
                else:
                    result_words.append(word_with_link)
                link_word = None
            else:
                if i == 0:
                    result_words.append(f"<b>{word}")
                elif i == 3:
                    result_words.append(f"{word}</b>")
                else:
                    result_words.append(word)
        else:
            if is_link_word and url:
                word_with_link = word.replace(link_word, f'<a href="{url}">{link_word}</a>')
                result_words.append(word_with_link)
                link_word = None
            else:
                result_words.append(word)
    
    if len(words) > 0 and len(words) < 4:
        result_words[-1] = result_words[-1] + "</b>"
    
    formatted_text = " ".join(result_words)
    
    if emoji_part:
        return f"{emoji_part} {formatted_text}"
    else:
        return formatted_text


def fetch_article_content(url: str) -> str | None:
    """Descarcă și extrage conținutul unui articol."""
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
        logger.error(f"Eroare la extragerea conținutului: {e}")
    return None


def generate_summary(content: str, url: str = None) -> tuple:
    """Generează rezumat folosind Claude API. Returnează (rezumat, eroare)."""
    try:
        if url:
            prompt = SUMMARY_PROMPT_WITH_URL.format(content=content[:15000])
        else:
            prompt = SUMMARY_PROMPT_NO_URL.format(content=content[:15000])
        
        logger.info(f"Trimit request la Claude API...")
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        raw_summary = message.content[0].text
        logger.info(f"Raw summary: {raw_summary}")
        
        formatted = format_summary_html(raw_summary, url)
        logger.info(f"Formatted summary: {formatted}")
        return formatted, None
    except anthropic.AuthenticationError as e:
        logger.error(f"Eroare autentificare: {e}")
        return None, "Cheie API invalidă sau expirată"
    except anthropic.RateLimitError as e:
        logger.error(f"Rate limit: {e}")
        return None, "Prea multe cereri. Așteaptă câteva secunde."
    except anthropic.APIError as e:
        logger.error(f"API Error: {e}")
        return None, f"Eroare API: {str(e)[:100]}"
    except Exception as e:
        logger.error(f"Eroare Claude API: {type(e).__name__}: {e}")
        return None, f"{type(e).__name__}: {str(e)[:100]}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler pentru comanda /start."""
    welcome_message = (
        "👋 Salut! Sunt botul tău pentru rezumate de știri.\n\n"
        "📝 <b>Cum mă folosești:</b>\n"
        "• Trimite un link către un articol\n"
        "• Trimite un text cu link-uri\n"
        "• Sau trimite direct text pentru rezumat\n\n"
        "✨ <b>Ce primești:</b>\n"
        "Un rezumat de 850-950 caractere în română, "
        "cu emoji relevant și formatare pentru Telegram/newsletter.\n\n"
        "🚀 Trimite primul mesaj!"
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler principal pentru mesaje."""
    
    text = update.message.text or update.message.caption or ""
    
    if not text.strip():
        await update.message.reply_text(
            "❌ Mesajul e gol. Trimite-mi un link sau un text."
        )
        return
    
    all_urls = extract_urls_from_entities(update.message)
    article_urls = filter_article_urls(all_urls)
    cleaned_text = clean_telegram_footer(text)
    
    logger.info(f"Original text length: {len(text)}, Cleaned text length: {len(cleaned_text)}")
    logger.info(f"All URLs: {all_urls}")
    logger.info(f"Article URLs: {article_urls}")
    
    if article_urls:
        url = article_urls[0]
        processing_msg = await update.message.reply_text("⏳ Procesez articolul...")
        
        content = fetch_article_content(url)
        
        if not content:
            await processing_msg.edit_text(
                "❌ Nu am putut extrage conținutul articolului. "
                "Verifică dacă link-ul e accesibil sau lipește textul direct."
            )
            return
        
        summary, error = generate_summary(content, url)
    else:
        if len(cleaned_text) < 50:
            await update.message.reply_text(
                "❌ Textul e prea scurt pentru un rezumat. Trimite cel puțin 50 de caractere."
            )
            return
        
        processing_msg = await update.message.reply_text("⏳ Procesez textul...")
        summary, error = generate_summary(cleaned_text, url=None)
    
    if not summary:
        error_msg = f"❌ Eroare la generarea rezumatului.\n\n🔍 Detalii: {error}" if error else "❌ Eroare la generarea rezumatului. Încearcă din nou."
        await processing_msg.edit_text(error_msg)
        return
    
    await processing_msg.edit_text(summary, parse_mode=ParseMode.HTML)


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
    
    logger.info("Botul pornește...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
