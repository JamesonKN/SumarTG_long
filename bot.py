"""
Telegram Bot pentru rezumate de articole
Comenzi: /scurt (250-300), /mediu (500-600), /lung (850-950)
Batch: max 7 linkuri → rezumate scurte
Default fără comandă: lung
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

# Inițializare client Anthropic
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Configurări lungimi
LENGTH_CONFIG = {
    "scurt": {"min": 250, "max": 300, "paragraphs": "1"},
    "mediu": {"min": 500, "max": 600, "paragraphs": "2"},
    "lung": {"min": 850, "max": 950, "paragraphs": "2-3"},
}

MAX_BATCH_LINKS = 7


def get_prompt(length_type: str, has_url: bool) -> str:
    """Generează prompt-ul în funcție de lungime și tip."""
    config = LENGTH_CONFIG.get(length_type, LENGTH_CONFIG["lung"])
    para_text = "un singur paragraf" if config["paragraphs"] == "1" else f"{config['paragraphs']} paragrafe scurte, separate prin linie goală"
    
    base_prompt = f"""Ești un editor de știri. Primești un {"articol" if has_url else "text"} și trebuie să creezi un rezumat în ROMÂNĂ.

REGULI STRICTE:
1. Rezumatul trebuie să aibă EXACT {config["min"]}-{config["max"]} de caractere (nu cuvinte, caractere!)
2. Scrie rezumatul în {para_text}
3. Începe cu un singur emoji relevant pentru subiect (politică=🏛️, economie=💰, tehnologie=💻, război/conflict=⚔️, UE=🇪🇺, Moldova=🇲🇩, România=🇷🇴, Rusia=🇷🇺, SUA=🇺🇸, sport=⚽, sănătate=🏥, mediu=🌍, etc.)
4. NU pune bold, italic sau alte formatări
5. NU pune link-uri în text
6. Scrie la persoana a 3-a, stil jurnalistic neutru
7. Dacă {"articolul" if has_url else "textul"} e în altă limbă, traduci rezumatul în română
{"8. Marchează UN SINGUR cuvânt cheie cu acolade, exemplu: {{atacat}} - acesta va deveni link" if has_url else ""}

{"ARTICOL" if has_url else "TEXT"}:
{{content}}

Răspunde DOAR cu rezumatul (emoji + text{"cu un cuvânt în acolade" if has_url else ""}), nimic altceva."""
    
    return base_prompt


def clean_telegram_footer(text: str) -> str:
    """Curăță footerele de Telegram."""
    footer_patterns = [
        r'Подписаться на .*$', r'Подпишись на .*$', r'Подписывайтесь.*$',
        r'Прислать контент.*$', r'Наш канал.*$', r'Читать далее.*$', r'Источник.*$',
        r'Subscribe to .*$', r'Follow us.*$', r'Join our.*$', r'Send content.*$',
        r'Abonează-te la .*$', r'Urmărește-ne.*$', r'Canalul nostru.*$', r'\s*\|\s*$',
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
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
    return cleaned_text.strip()


def extract_urls_from_entities(message) -> list:
    """Extrage URL-uri din mesaj."""
    urls = []
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    
    for entity in entities:
        if entity.type == MessageEntity.URL:
            urls.append(text[entity.offset:entity.offset + entity.length])
        elif entity.type == MessageEntity.TEXT_LINK:
            urls.append(entity.url)
    
    text_urls = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)
    urls.extend(text_urls)
    
    return list(dict.fromkeys(urls))  # Unique, păstrează ordinea


def filter_article_urls(urls: list) -> list:
    """Filtrează doar URL-uri către articole."""
    ignore_domains = ['t.me', 'telegram.me', 'twitter.com', 'x.com', 
                      'facebook.com', 'instagram.com', 'tiktok.com', 'youtube.com', 'youtu.be']
    
    article_urls = []
    for url in urls:
        try:
            domain = urlparse(url).netloc.lower()
            if not any(ignore in domain for ignore in ignore_domains):
                article_urls.append(url)
        except:
            pass
    return article_urls


def format_summary_html(summary: str, url: str = None) -> str:
    """Formatează rezumatul cu HTML."""
    summary = summary.replace("**", "").replace("*", "").replace("__", "")
    summary = summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # Separă emoji
    emoji_part = ""
    text_part = summary
    if len(summary) > 0 and not summary[0].isalnum() and summary[0] not in '([{':
        i = 0
        while i < len(summary) and not summary[i].isalnum():
            i += 1
        emoji_part = summary[:i].rstrip()
        text_part = summary[i:].lstrip()
    
    # Găsește cuvântul marcat
    link_word = None
    link_word_match = re.search(r'\{+([^}]+)\}+', text_part)
    if link_word_match:
        link_word = link_word_match.group(1)
        text_part = text_part[:link_word_match.start()] + link_word + text_part[link_word_match.end():]
    
    # Procesează paragrafe
    paragraphs = re.split(r'\n\s*\n|\n', text_part)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    
    formatted_paragraphs = []
    for para_idx, paragraph in enumerate(paragraphs):
        words = paragraph.split()
        result_words = []
        
        for word_idx, word in enumerate(words):
            is_link_word = link_word and link_word in word
            
            if word_idx < 3:
                if is_link_word and url:
                    word_with_link = word.replace(link_word, f'<a href="{url}">{link_word}</a>')
                    if word_idx == 0:
                        result_words.append(f"<b>{word_with_link}")
                    elif word_idx == 2:
                        result_words.append(f"{word_with_link}</b>")
                    else:
                        result_words.append(word_with_link)
                    link_word = None
                else:
                    if word_idx == 0:
                        result_words.append(f"<b>{word}")
                    elif word_idx == 2:
                        result_words.append(f"{word}</b>")
                    else:
                        result_words.append(word)
            else:
                if is_link_word and url:
                    result_words.append(word.replace(link_word, f'<a href="{url}">{link_word}</a>'))
                    link_word = None
                else:
                    result_words.append(word)
        
        if len(words) > 0 and len(words) < 3:
            result_words[-1] = result_words[-1] + "</b>"
        
        formatted_para = " ".join(result_words)
        if para_idx > 0:
            formatted_para = "(...) " + formatted_para
        formatted_paragraphs.append(formatted_para)
    
    formatted_text = "\n\n".join(formatted_paragraphs)
    return f"{emoji_part} {formatted_text}" if emoji_part else formatted_text


def fetch_article_content(url: str) -> str | None:
    """Descarcă și extrage conținutul unui articol."""
    try:
        # Metoda 1: Trafilatura standard
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            content = trafilatura.extract(downloaded, include_comments=False, include_tables=False, no_fallback=False)
            if content and len(content) > 100:
                return content
        
        # Metoda 2: Fallback Jina AI - pentru ORICE site care eșuează
        logger.info(f"Trafilatura eșuat, încerc Jina AI pentru: {url[:60]}")
        try:
            import httpx
            jina_url = f"https://r.jina.ai/{url}"
            response = httpx.get(jina_url, timeout=20.0, follow_redirects=True)
            if response.status_code == 200:
                content = response.text
                # Curăță markdown headers și formatare excesivă
                content = re.sub(r'^#+\s+', '', content, flags=re.MULTILINE)
                content = re.sub(r'\n{3,}', '\n\n', content)
                if len(content) > 200:
                    logger.info(f"✓ Jina AI SUCCESS: {len(content)} caractere")
                    return content
                else:
                    logger.warning(f"Jina AI: conținut prea scurt ({len(content)} char)")
            else:
                logger.warning(f"Jina AI HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"Jina AI eșuat: {type(e).__name__}: {str(e)[:50]}")
        
    except Exception as e:
        logger.error(f"Eroare extragere: {e}")
    
    return None


def generate_summary(content: str, url: str = None, length_type: str = "lung") -> tuple:
    """Generează rezumat. Returnează (rezumat, eroare)."""
    try:
        prompt_template = get_prompt(length_type, has_url=bool(url))
        prompt = prompt_template.format(content=content[:15000])
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        
        raw_summary = message.content[0].text
        formatted = format_summary_html(raw_summary, url)
        return formatted, None
        
    except anthropic.AuthenticationError:
        return None, "Cheie API invalidă"
    except anthropic.RateLimitError:
        return None, "Prea multe cereri"
    except anthropic.APIError as e:
        return None, f"Eroare API: {str(e)[:100]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler pentru /start."""
    welcome = (
        "👋 Salut! Sunt botul pentru rezumate de știri.\n\n"
        "📝 <b>Comenzi:</b>\n"
        "• <code>/scurt link</code> → 250-300 caractere\n"
        "• <code>/mediu link</code> → 500-600 caractere\n"
        "• <code>/lung link</code> → 850-950 caractere\n"
        "• Link fără comandă → lung (default)\n\n"
        "📦 <b>Batch:</b> Trimite până la 7 linkuri (pe linii separate) → rezumate scurte\n\n"
        "🚀 Trimite primul link!"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.HTML)


async def process_single_article(url: str, length_type: str) -> str:
    """Procesează un singur articol și returnează rezumatul."""
    content = fetch_article_content(url)
    if not content:
        return f"❌ Nu am putut extrage: {url[:50]}..."
    
    summary, error = generate_summary(content, url, length_type)
    if not summary:
        return f"❌ Eroare pentru {url[:50]}...: {error}"
    
    return summary


def get_relevant_emoji(text: str) -> str:
    """Determină emoji-ul relevant pe baza conținutului textului."""
    text_lower = text.lower()
    
    # Politică / Guvern
    if any(word in text_lower for word in ['parlament', 'guvern', 'ministru', 'deputat', 'legislativ', 'politic', 'alegeri', 'vot', 'lege', 'preşedinte', 'premier']):
        return '🏛️'
    
    # Moldova
    if any(word in text_lower for word in ['moldova', 'chișinău', 'chisinau', 'maia sandu', 'pas ']):
        return '🇲🇩'
    
    # România
    if any(word in text_lower for word in ['românia', 'romania', 'bucureşti', 'bucuresti', 'iohannis']):
        return '🇷🇴'
    
    # UE
    if any(word in text_lower for word in ['uniunea europeană', 'uniunea europeana', 'bruxelles', 'comisia europeană', 'ue ']):
        return '🇪🇺'
    
    # Rusia
    if any(word in text_lower for word in ['rusia', 'kremlin', 'moscova', 'putin']):
        return '🇷🇺'
    
    # SUA
    if any(word in text_lower for word in ['sua', 'statele unite', 'washington', 'america', 'trump', 'biden']):
        return '🇺🇸'
    
    # Franța
    if any(word in text_lower for word in ['franţa', 'franta', 'paris', 'macron', 'francez']):
        return '🇫🇷'
    
    # Război / Conflict / Armată
    if any(word in text_lower for word in ['război', 'razboi', 'conflict', 'militar', 'armată', 'armata', 'atac', 'arme', 'soldaţ', 'soldat']):
        return '⚔️'
    
    # Justiție / Lege
    if any(word in text_lower for word in ['judecător', 'judecator', 'tribunal', 'condamnat', 'sentinţă', 'sentinta', 'proces', 'procuror', 'avocat', 'instanţă', 'instanta']):
        return '⚖️'
    
    # Economie / Bani
    if any(word in text_lower for word in ['economie', 'bancă', 'banca', 'bani', 'preţ', 'pret', 'dolar', 'euro', 'inflație', 'inflatie', 'salariu', 'buget', 'fiscal']):
        return '💰'
    
    # Tehnologie / Digital
    if any(word in text_lower for word in ['tehnologie', 'tehnologic', 'digital', 'internet', 'computer', 'software', 'ai ', 'inteligență artificială', 'crypto', 'blockchain']):
        return '💻'
    
    # Sănătate / Medical
    if any(word in text_lower for word in ['sănătate', 'sanatate', 'medical', 'spital', 'doctor', 'pacient', 'boală', 'boala', 'virus', 'vaccin', 'tratament']):
        return '🏥'
    
    # Sport
    if any(word in text_lower for word in ['fotbal', 'meci', 'echipă', 'echipa', 'campionat', 'jucător', 'jucator', 'sport', 'olimpic', 'antrenor']):
        return '⚽'
    
    # Mediu / Natură
    if any(word in text_lower for word in ['mediu', 'climă', 'clima', 'poluare', 'ecologic', 'natură', 'natura', 'pădure', 'padure']):
        return '🌍'
    
    # Educație
    if any(word in text_lower for word in ['educaţie', 'educatie', 'şcoală', 'scoala', 'universitate', 'student', 'profesor', 'elev']):
        return '📚'
    
    # Transport / Auto
    if any(word in text_lower for word in ['maşină', 'masina', 'auto', 'trafic', 'şofer', 'sofer', 'drum', 'accident']):
        return '🚗'
    
    # Energie
    if any(word in text_lower for word in ['energie', 'electric', 'gaz', 'petrol', 'combustibil', 'centrală', 'centrala']):
        return '⚡'
    
    # Default - știri generale
    return '📰'


def ensure_emoji_in_summaries(summaries: list) -> list:
    """Asigură că fiecare rezumat are emoji la început (adaugă emoji relevant dacă lipsește)."""
    fixed_summaries = []
    
    for summary in summaries:
        # Skip mesaje de eroare
        if summary.startswith('❌'):
            fixed_summaries.append(summary)
            continue
        
        # Verifică dacă conține emoji ORIUNDE la început (ignoră HTML tags)
        # Elimină temporar HTML tags pentru a verifica emoji
        text_without_html = re.sub(r'<[^>]+>', '', summary[:50])  # Check primele 50 char fără HTML
        
        has_emoji = False
        # Verifică dacă PRIMELE caractere non-whitespace conțin emoji
        match = re.search(r'^[\s]*[\U0001F000-\U0001FFFF\u2600-\u26FF\u2700-\u27BF\U0001F900-\U0001F9FF\U0001F1E0-\U0001F1FF]', text_without_html)
        if match:
            has_emoji = True
        
        # Dacă nu are emoji, determină unul relevant și adaugă-l
        if not has_emoji:
            relevant_emoji = get_relevant_emoji(summary)
            logger.info(f"Adding relevant emoji {relevant_emoji} to: {summary[:50]}...")
            fixed_summaries.append(f"{relevant_emoji} {summary}")
        else:
            fixed_summaries.append(summary)
    
    return fixed_summaries

def remove_duplicate_emojis_in_batch(summaries: list) -> list:
    """Elimină TOATE emoji-urile duplicate dintr-o listă de rezumate (păstrează doar prima apariție)."""
    if not summaries or len(summaries) <= 1:
        return summaries
    
    cleaned_summaries = []
    seen_emojis = set()  # Track TOATE emoji-urile văzute
    
    for idx, summary in enumerate(summaries):
        # Skip mesaje de eroare (care încep cu ❌)
        if summary.startswith('❌'):
            cleaned_summaries.append(summary)
            continue
        
        # Extrage emoji-ul: orice caractere non-word, non-space, non-HTML la început
        current_emoji = None
        
        # Match orice caractere non-ASCII la început până la primul caracter alfanumeric, spațiu sau <
        match = re.match(r'^([\U0001F000-\U0001FFFF\u2600-\u26FF\u2700-\u27BF\U0001F900-\U0001F9FF\U0001F1E0-\U0001F1FF]+)\s*', summary)
        if match:
            current_emoji = match.group(1)
        
        logger.info(f"Batch #{idx}: emoji='{current_emoji}', seen={seen_emojis}")
        
        # Verifică dacă emoji-ul a mai apărut ORIUNDE în batch (nu doar consecutiv)
        if current_emoji and current_emoji in seen_emojis:
            logger.info(f"  ✂️ Eliminating duplicate (seen before): {current_emoji}")
            # Elimină emoji-ul
            cleaned_summary = re.sub(r'^[\U0001F000-\U0001FFFF\u2600-\u26FF\u2700-\u27BF\U0001F900-\U0001F9FF\U0001F1E0-\U0001F1FF]+\s*', '', summary, count=1)
            cleaned_summaries.append(cleaned_summary)
        else:
            cleaned_summaries.append(summary)
        
        # Adaugă emoji-ul în set-ul de "văzute"
        if current_emoji:
            seen_emojis.add(current_emoji)
    
    return cleaned_summaries


async def handle_length_command(update: Update, context: ContextTypes.DEFAULT_TYPE, length_type: str):
    """Handler comun pentru comenzile /scurt, /mediu, /lung."""
    text = update.message.text or ""
    
    # Extrage linkurile din mesaj (după comandă)
    urls = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)
    article_urls = filter_article_urls(urls)
    
    if not article_urls:
        await update.message.reply_text(f"❌ Folosește: /{length_type} https://link-articol.com")
        return
    
    processing_msg = await update.message.reply_text("⏳ Procesez...")
    
    # Un singur link
    if len(article_urls) == 1:
        summary = await process_single_article(article_urls[0], length_type)
        await processing_msg.edit_text(summary, parse_mode=ParseMode.HTML)
    else:
        # Batch - max 7, folosește tipul specificat
        urls_to_process = article_urls[:MAX_BATCH_LINKS]
        summaries = []
        
        for i, url in enumerate(urls_to_process):
            await processing_msg.edit_text(f"⏳ Procesez {i+1}/{len(urls_to_process)}...")
            summary = await process_single_article(url, length_type)
            summaries.append(summary)
        
        # Asigură că toate rezumatele au emoji (adaugă 📰 dacă lipsește)
        summaries = ensure_emoji_in_summaries(summaries)
        
        # Elimină emoji-uri duplicate
        summaries = remove_duplicate_emojis_in_batch(summaries)
        
        final_text = "\n\n".join(summaries)
        
        # Telegram are limită de 4096 caractere
        if len(final_text) > 4000:
            final_text = final_text[:4000] + "\n\n⚠️ Textul a fost trunchiat."
        
        await processing_msg.edit_text(final_text, parse_mode=ParseMode.HTML)


async def scurt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_length_command(update, context, "scurt")

async def mediu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_length_command(update, context, "mediu")

async def lung_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_length_command(update, context, "lung")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler pentru mesaje fără comandă."""
    text = update.message.text or update.message.caption or ""
    
    if not text.strip():
        await update.message.reply_text("❌ Mesajul e gol.")
        return
    
    all_urls = extract_urls_from_entities(update.message)
    article_urls = filter_article_urls(all_urls)
    
    if not article_urls:
        # Text fără URL - rezumat lung din text
        cleaned_text = clean_telegram_footer(text)
        if len(cleaned_text) < 50:
            await update.message.reply_text("❌ Textul e prea scurt.")
            return
        
        processing_msg = await update.message.reply_text("⏳ Procesez textul...")
        summary, error = generate_summary(cleaned_text, url=None, length_type="lung")
        
        if not summary:
            await processing_msg.edit_text(f"❌ Eroare: {error}")
            return
        
        await processing_msg.edit_text(summary, parse_mode=ParseMode.HTML)
        return
    
    processing_msg = await update.message.reply_text("⏳ Procesez...")
    
    # Un singur link - rezumat LUNG (default)
    if len(article_urls) == 1:
        summary = await process_single_article(article_urls[0], "lung")
        await processing_msg.edit_text(summary, parse_mode=ParseMode.HTML)
    else:
        # Batch - max 7, rezumate SCURTE
        urls_to_process = article_urls[:MAX_BATCH_LINKS]
        summaries = []
        
        for i, url in enumerate(urls_to_process):
            await processing_msg.edit_text(f"⏳ Procesez {i+1}/{len(urls_to_process)}...")
            summary = await process_single_article(url, "scurt")
            summaries.append(summary)
        
        # Asigură că toate rezumatele au emoji (adaugă 📰 dacă lipsește)
        summaries = ensure_emoji_in_summaries(summaries)
        
        # Elimină emoji-uri duplicate
        summaries = remove_duplicate_emojis_in_batch(summaries)
        
        final_text = "\n\n".join(summaries)
        
        if len(final_text) > 4000:
            final_text = final_text[:4000] + "\n\n⚠️ Textul a fost trunchiat."
        
        if len(article_urls) > MAX_BATCH_LINKS:
            final_text += f"\n\n⚠️ Am procesat doar primele {MAX_BATCH_LINKS} linkuri."
        
        await processing_msg.edit_text(final_text, parse_mode=ParseMode.HTML)


def main():
    """Pornește botul."""
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN nu e setat!")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY nu e setat!")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Comenzi
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("scurt", scurt_command))
    application.add_handler(CommandHandler("mediu", mediu_command))
    application.add_handler(CommandHandler("lung", lung_command))
    
    # Mesaje text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.FORWARDED, handle_message))
    
    logger.info("Botul pornește...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
