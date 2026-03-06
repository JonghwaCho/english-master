"""Text processing utilities for EnglishMaster."""

import re
import ssl
import urllib.request
import urllib.parse
import http.cookiejar
import html as html_lib


def generate_title(text, max_length=60):
    """Generate a title from the first sentence of text."""
    if not text or not text.strip():
        return "Untitled"

    text = text.strip()
    # Try to find the first sentence boundary
    m = re.search(r'[.!?]\s', text)
    if m:
        first_sentence = text[:m.start() + 1].strip()
    else:
        first_sentence = text.strip()

    # Truncate if too long
    if len(first_sentence) > max_length:
        truncated = first_sentence[:max_length]
        # Cut at last word boundary
        last_space = truncated.rfind(' ')
        if last_space > max_length // 2:
            truncated = truncated[:last_space]
        return truncated.rstrip('.,;:!?-') + '...'

    return first_sentence


def clean_pasted_text(text):
    """
    Clean pasted text by removing likely non-article content
    (ads, navigation, UI elements, etc.) from raw text.
    Works on line level for text that has newlines.
    """
    if not text:
        return text
    lines = text.split('\n')
    clean = [line for line in lines if not _is_junk_line(line.strip())]
    return '\n'.join(clean)


def filter_junk_sentences(sentences):
    """
    Filter out junk sentences (ads, navigation, etc.) from a list of sentences.
    Works on sentence level - use this AFTER split_into_sentences() to catch
    junk that was embedded in a single line of pasted text.
    Returns filtered list of sentences.
    """
    if not sentences:
        return sentences

    junk_sentence_patterns = [
        r'^(Share|Tweet|Pin|Email|Print|Comment|Subscribe|Sign [Uu]p|Log [Ii]n|Register)\b',
        r'^(Advertisement|Sponsored|Promoted|ADVERTISEMENT)',
        r'^(Read [Mm]ore|More [Ss]tories|Related|Recommended|Also [Rr]ead|You [Mm]ay [Aa]lso)\b',
        r'^(Follow [Uu]s|Like [Uu]s|Join our|Newsletter|Get [Oo]ur)\b',
        r'^(All [Rr]ights [Rr]eserved|Copyright|\u00a9)',
        r'^(Skip to|Jump to|Go to|Back to|Return to)\b',
        r'^(Accept|Reject|Allow|Deny|Got it|No thanks)\b',
        r'^(Menu|Search|Home|About|Contact|FAQ|Help|Sitemap)\s*$',
        r'(Subscribe|newsletter|notification).{0,40}$',
        r'cookie[s]?\s*(policy|settings|preferences|consent)',
        r'^(From Wikipedia|Retrieved from)\b',
        r'^(See also|References|External links|Further reading|Bibliography|Sources|Categories)\s*$',
        r'(Getty Images|Shutterstock|iStock|Unsplash|Pixabay)',
        r'^(Share this|Share on|Follow us on)\b',
        r'^(Click here|Tap here|Sign up for|Download our)\b',
    ]
    compiled = [re.compile(p, re.IGNORECASE) for p in junk_sentence_patterns]

    result = []
    for s in sentences:
        stripped = s.strip()
        if not stripped:
            continue
        # Skip very short non-sentences
        if len(stripped) < 10:
            continue
        # Check against junk patterns
        is_junk = False
        for pat in compiled:
            if pat.search(stripped):
                is_junk = True
                break
        if not is_junk:
            result.append(s)
    return result


def split_into_sentences(text):
    """
    Split text into sentences, handling abbreviations properly.
    Returns list of sentence strings.
    """
    if not text or not text.strip():
        return []

    # Split by newlines first
    paragraphs = re.split(r'\n\n+|\n', text)
    raw_sentences = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Split on sentence-ending punctuation followed by space + uppercase letter or quote
        parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\u201C(])', para)
        # Merge back fragments that are too short (likely abbreviation splits)
        merged = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if merged and len(merged[-1]) < 20 and merged[-1][-1] not in '.!?':
                merged[-1] = merged[-1] + ' ' + part
            elif merged and len(part) < 15 and not any(c in part for c in '.!?'):
                merged[-1] = merged[-1] + ' ' + part
            else:
                merged.append(part)
        raw_sentences.extend(merged)

    return [s.strip() for s in raw_sentences if s.strip() and len(s.strip()) > 3]


def group_into_paragraphs(sentences, per_paragraph=5):
    """
    Assign paragraph indices to sentences.
    Returns list of (paragraph_idx, sentence_idx, text, None, None).
    """
    result = []
    para_idx = 0
    sent_idx = 0
    for s in sentences:
        result.append((para_idx, sent_idx, s, None, None))
        sent_idx += 1
        if sent_idx >= per_paragraph:
            para_idx += 1
            sent_idx = 0
    return result


def _strip_tags_to_text(html_fragment):
    """Helper: strip HTML tags from a fragment and return clean text lines."""
    text = html_fragment
    # Replace <br>, <p>, <div>, <li>, <h*> with newlines for structure
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|li|h[1-6]|tr|blockquote|figcaption)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<(p|div|li|h[1-6]|tr|blockquote)[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities
    text = html_lib.unescape(text)
    # Normalize whitespace per line
    lines = []
    for line in text.split('\n'):
        line = ' '.join(line.split()).strip()
        if line:
            lines.append(line)
    return lines


def _is_junk_line(line):
    """Check if a line looks like ads, navigation, UI elements, or non-article content."""
    stripped = line.strip()
    if not stripped:
        return True

    # Long lines likely contain multiple sentences mixed together;
    # sentence-level filtering (filter_junk_sentences) will handle those.
    if len(stripped) > 150:
        return False

    # Too short to be a real sentence (likely button/link/label)
    if len(stripped) < 15:
        # Allow short lines only if they look like headings (capitalized, no special chars)
        if not re.match(r'^[A-Z][A-Za-z\s\-:,]{5,}$', stripped):
            return True

    # Common ad/banner/UI patterns
    junk_patterns = [
        r'^(Share|Tweet|Pin|Email|Print|Comment|Subscribe|Sign [Uu]p|Log [Ii]n|Register)',
        r'^(Advertisement|Sponsored|Promoted|Ad|ADVERTISEMENT)',
        r'^(Read [Mm]ore|More [Ss]tories|Related|Recommended|Also [Rr]ead|You [Mm]ay [Aa]lso)',
        r'^(Follow [Uu]s|Like [Uu]s|Join|Newsletter|Get [Oo]ur)',
        r'^(All [Rr]ights [Rr]eserved|Copyright|\u00a9|Terms|Privacy|Cookie|Disclaimer)',
        r'^(Skip to|Jump to|Go to|Back to|Return to)',
        r'^(Photo|Image|Video|Audio|Credit|Getty|Reuters|AP Photo|AFP)',
        r'^(Facebook|Twitter|Instagram|LinkedIn|YouTube|WhatsApp|Telegram|Reddit)',
        r'^(Breaking|BREAKING|LIVE|WATCH|LISTEN|CLICK|TAP|SWIPE)',
        r'^(Menu|Search|Home|About|Contact|FAQ|Help|Support|Sitemap)',
        r'^(Previous|Next|First|Last|Page \d|Show \d|Load \d)',
        r'(Subscribe|newsletter|notification|popup|Sign up).{0,30}$',
        r'^(Accept|Reject|Allow|Deny|Got it|OK|Close|Dismiss|No thanks)',
        r'cookie[s]?\s*(policy|settings|preferences|consent)',
        r'^[\u2022\u2023\u25E6\u2043\u2219\u25CF\u25CB\u25AA\u25AB]\s',  # bullet point symbols only (not dashes)
        r'^\w+@\w+\.\w+$',  # email addresses
        r'^https?://',  # bare URLs
        r'^[\d,]+\s*(views|likes|comments|shares|followers|subscribers|retweets)',
        r'(Getty Images|Shutterstock|iStock|Unsplash|Pixabay)',
        r'^(Updated|Published|Posted|Modified|Edited|Written by|By )\s',
        r'^\d{1,2}[:/]\d{2}\s*(AM|PM|am|pm)',  # timestamps
        r'^From Wikipedia',  # Wikipedia meta
        r'^This article\s+(is about|duplicates|needs|may|has|contains|includes|relies)',
        r'^(See also|References|External links|Further reading|Bibliography|Sources|Categories)',
        r'^(Articles?\s+(with|lacking|from|needing|containing))',
        r'^(All articles|Pages using|Webarchive|CS1\s)',
        r'^(Commons category|Wikidata|Coordinates)',
        r'^(This (page|section|article) (was|is|needs|may))',
        r'^\[edit\]$',
        r'^(Main article|See also|For other uses|Not to be confused)',
        r'^\^',  # footnote references
        r'^Retrieved from\s',  # Wikipedia footer
        r'^(Short description|Use \w+ dates from)',
        r'^(Cite \w+|Webarchive template)',
    ]
    for pat in junk_patterns:
        if re.search(pat, stripped, re.IGNORECASE):
            return True

    # Lines that are mostly special characters or numbers (not real sentences)
    alpha_count = sum(1 for c in stripped if c.isalpha())
    if len(stripped) > 0 and alpha_count / len(stripped) < 0.4:
        return True

    # Very short phrases likely menus or breadcrumbs (but not headings)
    words = stripped.split()
    if len(words) <= 2 and len(stripped) < 20:
        return True

    return False


def extract_text_from_html(html_content):
    """
    Extract main article text from HTML, filtering out ads, banners,
    navigation, sidebars, and other non-article content.
    Uses regex-based approach (no external deps).
    """
    if not html_content:
        return ""

    # --- Phase 1: Try to find the main article container ---
    article_html = None

    # Try <article> tag first (most news/blog sites)
    m = re.search(r'<article[^>]*>(.*?)</article>', html_content, re.DOTALL | re.IGNORECASE)
    if m and len(m.group(1)) > 200:
        article_html = m.group(1)

    # Try [role="main"] or <main>
    if not article_html:
        m = re.search(r'<[^>]+role\s*=\s*["\']main["\'][^>]*>(.*?)</(?:div|main|section)>', html_content, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 200:
            article_html = m.group(1)
    if not article_html:
        m = re.search(r'<main[^>]*>(.*?)</main>', html_content, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 200:
            article_html = m.group(1)

    # Try common article container class/id patterns
    if not article_html:
        content_patterns = [
            r'class\s*=\s*["\'][^"\']*\b(article[-_]?body|article[-_]?content|story[-_]?body|story[-_]?content|post[-_]?body|post[-_]?content|entry[-_]?content|main[-_]?content)\b[^"\']*["\']',
        ]
        for pat in content_patterns:
            m = re.search(r'<div[^>]*' + pat + r'[^>]*>(.*?)</div>', html_content, re.DOTALL | re.IGNORECASE)
            if m and len(m.group(1)) > 200:
                article_html = m.group(1)
                break

    # Fallback: use the full HTML <body> content
    if not article_html:
        m = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
        article_html = m.group(1) if m else html_content

    # --- Phase 2: Remove non-content blocks from the extracted HTML ---
    text = article_html

    # Remove common non-content tags entirely
    for tag in ['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript',
                'iframe', 'form', 'button', 'input', 'select', 'textarea', 'svg',
                'figure', 'figcaption', 'picture', 'video', 'audio', 'canvas',
                'template', 'dialog', 'menu', 'menuitem']:
        text = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(rf'<{tag}[^>]*/?\s*>', '', text, flags=re.IGNORECASE)

    # Remove elements with ad/sidebar/promo related class or id
    ad_class_patterns = [
        r'ad[-_\s]?(?:slot|banner|container|wrapper|block|unit|box|zone|holder|placement|leaderboard|sidebar|widget|rail|promo|related|recommend|newsletter|signup|social|share|comment|disqus|outbrain|taboola|mgid|zergnet|recirculation|trending|popular|footer|nav|menu|breadcrumb|cookie|consent|modal|popup|overlay|notification|alert|toast)',
    ]
    for pat in ad_class_patterns:
        text = re.sub(
            r'<(?:div|section|aside|span|ul|ol|figure|iframe)[^>]*(?:class|id)\s*=\s*["\'][^"\']*\b' + pat + r'\b[^"\']*["\'][^>]*>.*?</(?:div|section|aside|span|ul|ol|figure|iframe)>',
            '', text, flags=re.DOTALL | re.IGNORECASE
        )

    # --- Phase 3: Convert to plain text ---
    lines = _strip_tags_to_text(text)

    # --- Phase 4: Filter junk lines ---
    clean_lines = [line for line in lines if not _is_junk_line(line)]

    # --- Phase 5: Fallback if filtering removed too much ---
    # If filtered result is too short but unfiltered has real content,
    # use a lighter filter (only remove very short lines)
    filtered_text = '\n'.join(clean_lines)
    unfiltered_text = '\n'.join(lines)
    if len(filtered_text.strip()) < 100 and len(unfiltered_text.strip()) > 200:
        light_lines = [l for l in lines if len(l.strip()) > 20]
        return '\n'.join(light_lines)

    return filtered_text


def extract_title_from_html(html_content):
    """Extract <title> tag content from HTML using regex."""
    if not html_content:
        return None
    m = re.search(r'<title[^>]*>(.*?)</title>', html_content, re.IGNORECASE | re.DOTALL)
    if m:
        title = m.group(1).strip()
        title = html_lib.unescape(title)
        title = re.sub(r'\s+', ' ', title).strip()
        # Remove common suffixes like " - BBC News", " | Reuters"
        title = re.split(r'\s*[\|–—-]\s*(?=[A-Z])', title)[0].strip()
        if title and len(title) > 3:
            return title
    return None


def fetch_url_content(url, timeout=15):
    """
    Fetch webpage content.
    Returns (html_content, final_url).
    Raises ValueError for errors.
    """
    if not url or not url.strip():
        raise ValueError("URL을 입력하세요")

    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'identity',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    try:
        # Set up cookie handling and SSL context for broader compatibility
        cookie_jar = http.cookiejar.CookieJar()
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar),
            urllib.request.HTTPSHandler(context=ssl_ctx),
        )

        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=timeout) as resp:
            content_type = resp.headers.get('Content-Type', '')
            # Accept various HTML-like content types
            allowed = ('text/html', 'text/plain', 'application/xhtml+xml', 'application/xml')
            if not any(t in content_type for t in allowed):
                raise ValueError(f"HTML이 아닌 콘텐츠입니다: {content_type}")

            # Detect encoding
            charset = 'utf-8'
            if 'charset=' in content_type:
                charset = content_type.split('charset=')[-1].split(';')[0].strip()

            # Also check <meta charset> in first chunk
            data = resp.read(5 * 1024 * 1024)
            try:
                html = data.decode(charset)
            except (UnicodeDecodeError, LookupError):
                # Try to detect charset from HTML meta tag
                raw_head = data[:2048].decode('ascii', errors='ignore')
                meta_m = re.search(r'charset=["\']?([a-zA-Z0-9_-]+)', raw_head)
                if meta_m:
                    try:
                        html = data.decode(meta_m.group(1))
                    except (UnicodeDecodeError, LookupError):
                        html = data.decode('utf-8', errors='replace')
                else:
                    html = data.decode('utf-8', errors='replace')

            return html, resp.url
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ValueError(f"이 사이트는 자동 접근을 차단합니다 (403). 텍스트를 직접 복사하여 붙여넣기를 이용하세요.")
        elif e.code == 404:
            raise ValueError(f"페이지를 찾을 수 없습니다 (404). URL을 확인하세요.")
        raise ValueError(f"HTTP 오류 {e.code}: URL을 확인하세요")
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if 'SSL' in reason or 'certificate' in reason.lower():
            raise ValueError(f"SSL 인증서 오류: 이 사이트에 안전하게 접속할 수 없습니다")
        raise ValueError(f"URL 연결 실패: 주소를 확인하세요")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"페이지 가져오기 실패: {str(e)}")


def extract_text_from_file(file_obj, filename):
    """
    Extract text from an uploaded file based on its extension.
    file_obj: file-like object (werkzeug FileStorage or similar)
    filename: original filename for extension detection
    Returns: extracted text string
    Raises: ValueError for unsupported file types
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext == 'txt':
        raw = file_obj.read()
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('latin-1', errors='replace')

    elif ext == 'pdf':
        try:
            from pypdf import PdfReader
            import io
            pdf_data = file_obj.read()
            reader = PdfReader(io.BytesIO(pdf_data))
            texts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    texts.append(page_text)
            if not texts:
                raise ValueError("PDF에서 텍스트를 추출할 수 없습니다 (이미지 기반 PDF일 수 있습니다)")
            return '\n'.join(texts)
        except ImportError:
            raise ValueError("PDF 지원을 위해 'pypdf' 패키지가 필요합니다: pip install pypdf")

    elif ext in ('html', 'htm'):
        raw = file_obj.read()
        try:
            html = raw.decode('utf-8')
        except UnicodeDecodeError:
            html = raw.decode('latin-1', errors='replace')
        return extract_text_from_html(html)

    else:
        raise ValueError(f"지원하지 않는 파일 형식입니다: .{ext} (지원: .txt, .pdf, .html)")
