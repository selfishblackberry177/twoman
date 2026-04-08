#!/usr/bin/env python3

import argparse
import hashlib
import html
import json
import random
import re

SLUG_PARTS = [
    "afaq", "arya", "baran", "darya", "didar", "faraz", "farda", "honar",
    "mahsa", "mehr", "namad", "negar", "parvaz", "rahyar", "saba", "sahar",
    "sepand", "setareh", "shabnam", "roya", "omid", "tara"
]

ROUTE_PARTS = [
    "arzyabi", "asnad", "didban", "enteshar", "ertebat", "farayand",
    "gozaresh", "hamahang", "hamrah", "khedmat", "modiriyat", "negah",
    "payesh", "peyvand", "rahkar", "resaneh", "samaneh", "tasvir"
]

# Random themes: Corporate, Gaming, Creative, E-Commerce
THEMES = {
    "corporate": {
        "headlines": [
            "توسعه پایدار و خدمات استراتژیک",
            "گروه مهندسی و مدیریت داده",
            "راهکارهای سازمانی آینده‌نگر",
            "بستر هوشمند مدیریت منابع مالی",
            "سامانه جامع ارزیابی و کنترل فروش"
        ],
        "subheads": [
            "تیم ما با بهره‌گیری از جدیدترین تکنولوژی‌ها، مسیر موفقیت تجاری شما را هموار می‌سازد.",
            "راه‌حل‌های نوین و داده‌محور برای سازمان‌هایی که به دنبال بهره‌وری حداکثری هستند.",
            "همراهی مطمئن از اولین قدم تا استقرار و پشتیبانی مداوم سامانه‌های نرم‌افزاری.",
            "ارائه دهنده راهکارهای یکپارچه ارتباط با مشتریان و مدیریت فرآیندهای مالی."
        ],
        "cards": [
            ("مشاوره تخصصی", "ارائه مشاوره استراتژیک و نقشه راه برای ارتقای زیرساخت فناوری سازمان."),
            ("توسعه پلتفرم", "پیاده‌سازی سریع سامانه‌های مقیاس‌پذیر بر پایه معماری میکروسرویس."),
            ("امنیت داده", "نظارت مداوم و استقرار پروتکل‌های حفاظتی برای اطلاعات حساس شرکتی."),
            ("آموزش پرسنل", "برگزاری دوره‌های تخصصی جهت ارتقای دانش نرم‌افزاری تیم فروش."),
            ("گزارش‌گیری تحلیلی", "تولید دشبوردهای زنده برای رصد و پایش شاخص‌های عملکردی کلیدی."),
            ("پشتیبانی ابری", "میزبانی مطمئن، بک‌آپ منظم و مانیتورینگ ۲۴ ساعته سرورهای سازمانی.")
        ]
    },
    "gaming": {
        "headlines": [
            "باشگاه بازی و رسانه تعاملی",
            "مرکز پخش رویدادها و محتوای گیمینگ",
            "پلتفرم هماهنگی مسابقات و محتوای تصویری",
            "لیگ برتر ورزش‌های الکترونیک",
            "دنیای بی‌پایان مسابقات تعاملی"
        ],
        "subheads": [
            "این سایت برای معرفی برنامه‌های گیم‌نت، پخش ویدیو، پادکست و اطلاع‌رسانی رویدادهای داخلی طراحی شده است.",
            "بازیکن‌ها و تیم اجرایی می‌توانند از همین صفحه به برنامه مسابقات، محتوای ویدیویی و بخش اطلاعیه‌ها دسترسی داشته باشند.",
            "به جدیدترین تورنمنت‌ها بپیوندید و مهارت خود را در فضایی هیجان‌انگیز به چالش بکشید.",
            "استریم مسابقات زنده، تحلیل‌های تخصصی و بررسی جدیدترین تجهیزات گیمینگ روز."
        ],
        "cards": [
            ("اتاق مسابقات", "زمان‌بندی تورنمنت‌های دوستانه و ثبت نتیجه بازی‌ها در پایان هر سانس."),
            ("پخش ویدیو", "نمایش کلیپ‌های آموزشی، گلچین بازی‌ها و تریلر رویدادهای آینده."),
            ("رسانه باشگاه", "انتشار خبرهای روزانه، گزارش برنامه‌ها و اطلاعیه‌های اجرایی."),
            ("باشگاه مشتریان", "مدیریت طرح‌های عضویت، تخفیف‌ها و امتیازهای فصلی برای کاربران."),
            ("رزرو هوشمند", "رزرو آنلاین سیستم، مشاهده ظرفیت سالن و انتخاب زمان حضور."),
            ("سرور اختصاصی", "پینگ پایین و پایداری شبکه برای تجربه‌ای روان و بدون افت کیفیت.")
        ]
    },
    "creative": {
        "headlines": [
            "آژانس دیجیتال و طراحی خلاقانه",
            "استودیوی برندینگ و خلق تجربه‌های بصری",
            "طراحی هنر و رسانه‌های دیجیتال رویا",
            "نوآوری در قلب طراحی و تولید محتوا"
        ],
        "subheads": [
            "ما با ترکیب هنر و تکنولوژی، داستان برند شما را به زیباترین شکل ممکن روایت می‌کنیم.",
            "ارائه خدمات جامع طراحی تا تولید محتوای شبکه‌های اجتماعی برای درخشش در بازار.",
            "پرتفولیوی تخصصی از برترین آثار بصری، انیمیشن و ویدیوهای تبلیغاتی خلاقانه.",
            "استودیویی اختصاصی برای خلق هویت بصری منحصر‌به‌فرد و ماندگار."
        ],
        "cards": [
            ("طراحی رابط کاربری", "خلق فضایی کاربرپسند و چشم‌نواز برای وب‌سایت‌ها و اپلیکیشن‌ها."),
            ("تولید محتوا", "عکاسی صنعتی، ضبط ویدیو تبلیغاتی و طراحی گرافیک شبکه‌های اجتماعی."),
            ("برندینگ", "طراحی هویت بصری، لوگو، رنگ‌سازمانی و استراتژی ارتباط با مخاطب."),
            ("کپی‌رایتینگ", "نگارش متون اثرگذار و شعارهای تبلیغاتی متناسب با لحن برند شما."),
            ("طراحی سه‌بعدی", "مدل‌سازی محصولات و خلق انیمیشن‌های مینیمال برای معرفی خدمات."),
            ("پادکست اختصاصی", "ضبط صدا با تجهیزات حرفه‌ای و تدوین پیشرفته جهت برندسازی صوتی.")
        ]
    }
}

FOOTERS = [
    "چیدمان این صفحه در هر استقرار متناسب با نیاز مجموعه به‌روزرسانی می‌شود.",
    "نسخه فعلی برای ارائه تجربه یکپارچه در تمام دستگاه‌ها تنظیم شده است.",
    "تمامی حقوق این محتوا محفوظ بوده و هرگونه کپی‌برداری پیگرد قانونی دارد.",
    "طراحی و بهینه‌سازی شده با افتخار برای کاربران فارسی‌زبان."
]

PALETTES = [
    {"bg_a": "#0F2027", "bg_b": "#203A43", "bg_c": "#2C5364", "ink": "#FFFFFF", "accent": "#fbc531", "card_bg": "rgba(255, 255, 255, 0.05)", "card_border": "rgba(255,255,255,0.1)", "glass_blur": "12px", "blob_a": "#3498db", "blob_b": "#9b59b6"},
    {"bg_a": "#fdfbfb", "bg_b": "#ebedee", "bg_c": "#e0eafc", "ink": "#2d3436", "accent": "#00cec9", "card_bg": "rgba(255, 255, 255, 0.7)", "card_border": "rgba(0,0,0,0.05)", "glass_blur": "20px", "blob_a": "#74ebd5", "blob_b": "#ACB6E5"},
    {"bg_a": "#232526", "bg_b": "#414345", "bg_c": "#1b1e23", "ink": "#f1f2f6", "accent": "#e84118", "card_bg": "rgba(0, 0, 0, 0.3)", "card_border": "rgba(255,255,255,0.05)", "glass_blur": "8px", "blob_a": "#c23616", "blob_b": "#e1b12c"},
    {"bg_a": "#ECE9E6", "bg_b": "#FFFFFF", "bg_c": "#f1f2f6", "ink": "#2f3640", "accent": "#8c7ae6", "card_bg": "rgba(255, 255, 255, 0.85)", "card_border": "rgba(0,0,0,0.08)", "glass_blur": "16px", "blob_a": "#9c88ff", "blob_b": "#fbc531"},
    {"bg_a": "#000428", "bg_b": "#004e92", "bg_c": "#002b5e", "ink": "#dfe6e9", "accent": "#00b894", "card_bg": "rgba(255, 255, 255, 0.08)", "card_border": "rgba(255,255,255,0.15)", "glass_blur": "25px", "blob_a": "#1abc9c", "blob_b": "#2ecc71"}
]

VIDEO_SOURCES = [
    "https://samplelib.com/lib/preview/mp4/sample-10s.mp4",
    "https://samplelib.com/lib/preview/mp4/sample-15s.mp4",
    "https://samplelib.com/lib/preview/mp4/sample-20s.mp4",
]

AUDIO_SOURCES = [
    "https://samplelib.com/lib/preview/mp3/sample-3s.mp3",
    "https://samplelib.com/lib/preview/mp3/sample-6s.mp3",
    "https://samplelib.com/lib/preview/mp3/sample-9s.mp3",
]


def normalize_site_name(name):
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return ""
    # Keep Persian/Arabic letters, Latin letters, numbers, spaces and a minimal set of punctuation.
    cleaned = re.sub(r"[^0-9A-Za-z\u0600-\u06FF \-_.،]", "", cleaned)
    return " ".join(cleaned.split())


def choose_unique(rng, items, count):
    pool = list(items)
    rng.shuffle(pool)
    return pool[:count]


def ascii_slug(*parts):
    return "-".join(part.strip("-") for part in parts if part).strip("-")


def generate_fake_phone(rng):
    return f"021-{rng.randint(80000000, 89999999)}"

def generate_fake_address(rng):
    streets = ["میدان ونک، خیابان گاندی", "خیابان ولیعصر، بالاتر از پارک ساعی", "بلوار میرداماد، جنب پایتخت", "سعادت‌آباد، سرو غربی", "شهرک غرب، بلوار فرحزادی", "خیابان آزادی، بعد از یادگار", "خیابان شریعتی، نرسیده به تجریش"]
    return f"تهران، {rng.choice(streets)}، پلاک {rng.randint(1, 100)}، طبقه {rng.randint(1, 10)}"

def generate_fake_email(site_slug):
    domain = f"{site_slug.split('-')[0]}.com" if "-" in site_slug else f"{site_slug}.com"
    return f"info@{domain}"


def build_manifest(deployment_id, site_name=""):
    digest = hashlib.sha256(deployment_id.encode("utf-8")).hexdigest()
    rng = random.Random(digest)
    
    slug_parts = choose_unique(rng, SLUG_PARTS, 2)
    site_slug = ascii_slug(slug_parts[0], slug_parts[1], digest[:4])
    
    route_parts = choose_unique(rng, ROUTE_PARTS, 4)
    site_root_path = "/" + site_slug
    passenger_base_path = site_root_path + "/" + ascii_slug(route_parts[0], route_parts[1])
    node_base_path = site_root_path + "/" + ascii_slug(route_parts[2], route_parts[3])
    
    theme_key = rng.choice(list(THEMES.keys()))
    theme = THEMES[theme_key]
    
    headline = rng.choice(theme["headlines"])
    subhead = rng.choice(theme["subheads"])
    footer = rng.choice(FOOTERS)
    palette = rng.choice(PALETTES)
    
    cards = choose_unique(rng, theme["cards"], 3)
    video_sources = choose_unique(rng, VIDEO_SOURCES, 2)
    audio_source = rng.choice(AUDIO_SOURCES)
    
    display_name = normalize_site_name(site_name) or headline
    
    phone = generate_fake_phone(rng)
    address = generate_fake_address(rng)
    email = generate_fake_email(site_slug)
    
    context = {
        "deployment_id": deployment_id,
        "site_slug": site_slug,
        "site_name": display_name,
        "subhead": subhead,
        "footer": footer,
        "palette": palette,
        "cards": cards,
        "video_sources": video_sources,
        "audio_source": audio_source,
        "phone": phone,
        "address": address,
        "email": email
    }
    
    html_index = render_page("index", context)
    html_about = render_page("about", context)
    html_contact = render_page("contact", context)
    html_404 = render_page("404", context)
    
    robots_txt = f"User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n"
    
    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/</loc>
    <changefreq>weekly</changefreq>
  </url>
  <url>
    <loc>https://example.com/about.html</loc>
    <changefreq>monthly</changefreq>
  </url>
  <url>
    <loc>https://example.com/contact.html</loc>
    <changefreq>monthly</changefreq>
  </url>
</urlset>"""
    
    return {
        "deployment_id": deployment_id,
        "site_name": display_name,
        "site_slug": site_slug,
        "site_root_path": site_root_path,
        "site_index_relative_path": site_slug + "/index.html",
        "passenger_base_path": passenger_base_path,
        "node_base_path": node_base_path,
        "php_base_path": passenger_base_path,
        "landing_html": html_index,
        "about_html": html_about,
        "contact_html": html_contact,
        "404_html": html_404,
        "robots_txt": robots_txt,
        "sitemap_xml": sitemap_xml
    }


def render_page(page_type, ctx):
    site_name = html.escape(ctx["site_name"])
    deployment_id = html.escape(ctx["deployment_id"])
    subhead = html.escape(ctx["subhead"])
    footer = html.escape(ctx["footer"])
    phone = html.escape(ctx["phone"])
    address = html.escape(ctx["address"])
    email = html.escape(ctx["email"])
    palette = ctx["palette"]
    
    delay_stagger = 0.1
    card_markup = ""
    for idx, (title, body) in enumerate(ctx.get("cards", [])):
        anim_delay = (idx + 1) * delay_stagger
        card_markup += f"""
        <article class="feature-card reveal" style="animation-delay: {anim_delay}s">
          <div class="card-icon"></div>
          <h3>{html.escape(title)}</h3>
          <p>{html.escape(body)}</p>
        </article>
        """

    base_css = """
    :root {{
      --bg-a: {bg_a};
      --bg-b: {bg_b};
      --bg-c: {bg_c};
      --ink: {ink};
      --accent: {accent};
      --card-bg: {card_bg};
      --card-border: {card_border};
      --glass-blur: {glass_blur};
      --blob-a: {blob_a};
      --blob-b: {blob_b};
    }}
    
    * {{ box-sizing: border-box; }}
    
    body, html {{
      margin: 0;
      padding: 0;
      font-family: 'Vazirmatn', Tahoma, sans-serif;
      color: var(--ink);
      min-height: 100vh;
      overflow-x: hidden;
      background: linear-gradient(135deg, var(--bg-a) 0%, var(--bg-b) 50%, var(--bg-c) 100%);
      background-attachment: fixed;
    }}

    /* Animated background blobs */
    .bg-shape {{
        position: fixed;
        filter: blur(80px);
        opacity: 0.5;
        z-index: -1;
        border-radius: 50%;
        animation: float 20s infinite ease-in-out alternate;
    }}
    .bg-shape-1 {{
        top: -10%;
        left: -10%;
        width: 50vw;
        height: 50vw;
        background: var(--blob-a);
        animation-delay: -2s;
    }}
    .bg-shape-2 {{
        bottom: -20%;
        right: -10%;
        width: 60vw;
        height: 60vw;
        background: var(--blob-b);
        animation-delay: -7s;
    }}

    @keyframes float {{
        0% {{ transform: translate(0, 0) scale(1); }}
        50% {{ transform: translate(5%, 10%) scale(1.1); }}
        100% {{ transform: translate(-5%, -5%) scale(0.9); }}
    }}

    .shell {{
      width: min(1200px, 90%);
      margin: 0 auto;
      padding: 40px 0 80px;
    }}

    /* Glassmorphism primitives */
    .glass {{
      background: var(--card-bg);
      backdrop-filter: blur(var(--glass-blur));
      -webkit-backdrop-filter: blur(var(--glass-blur));
      border: 1px solid var(--card-border);
      border-radius: 24px;
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.15);
    }}

    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 32px;
      margin-bottom: 60px;
      position: sticky;
      top: 20px;
      z-index: 100;
      animation: slideDown 0.8s ease-out forwards;
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
      font-size: 20px;
      letter-spacing: -0.5px;
      color: var(--ink);
      text-decoration: none;
    }}

    .brand-badge {{
      width: 40px;
      height: 40px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--accent), var(--blob-a));
      box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }}

    .menu {{
      display: flex;
      gap: 24px;
    }}

    .menu a {{
      color: var(--ink);
      text-decoration: none;
      font-size: 15px;
      font-weight: 600;
      position: relative;
    }}

    .menu a::after {{
      content: '';
      position: absolute;
      width: 0;
      height: 2px;
      bottom: -4px;
      left: 0;
      background-color: var(--accent);
      transition: width 0.3s ease;
    }}

    .menu a:hover::after {{
      width: 100%;
    }}

    .hero {{
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 30px;
      margin-bottom: 60px;
    }}

    .hero-main {{
      padding: 50px 40px;
      animation: fadeInUp 1s ease-out forwards;
    }}

    .hero-side {{
      padding: 40px 30px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 20px;
      animation: fadeInUp 1s ease-out 0.2s forwards;
      opacity: 0;
    }}

    .hero-kicker {{
      display: inline-block;
      padding: 8px 18px;
      border-radius: 30px;
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid var(--card-border);
      font-size: 14px;
      font-weight: 600;
      margin-bottom: 24px;
      color: var(--accent);
    }}

    h1 {{
      margin: 0 0 20px;
      font-size: clamp(40px, 5vw, 64px);
      line-height: 1.1;
      font-weight: 800;
      background: linear-gradient(135deg, var(--ink), var(--blob-b));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}

    .hero-main p, .content-main p {{
      margin: 0 0 30px;
      font-size: 18px;
      line-height: 1.8;
      opacity: 0.85;
    }}

    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 14px 28px;
      border-radius: 12px;
      font-weight: 800;
      font-size: 16px;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.3s ease;
      border: none;
    }}

    .btn-primary {{
      background: var(--accent);
      color: #111;
      box-shadow: 0 8px 20px rgba(0,0,0,0.2);
    }}

    .btn-primary:hover {{
      transform: translateY(-3px);
      box-shadow: 0 12px 25px rgba(0,0,0,0.3);
      filter: brightness(1.1);
    }}

    .btn-secondary {{
      background: transparent;
      color: var(--ink);
      border: 2px solid var(--card-border);
      margin-right: 16px;
    }}

    .btn-secondary:hover {{
      background: var(--card-bg);
      transform: translateY(-3px);
    }}

    .mini-stat {{
      padding: 20px;
      border-radius: 16px;
      background: rgba(0,0,0,0.1);
      border: 1px solid var(--card-border);
      transition: transform 0.3s ease;
    }}

    .mini-stat:hover {{
      transform: translateX(-5px);
      background: rgba(0,0,0,0.15);
    }}

    .mini-stat strong {{
      display: block;
      color: var(--accent);
      font-size: 32px;
      margin-bottom: 8px;
    }}

    .mini-stat span {{
      font-size: 14px;
      opacity: 0.8;
    }}

    .section-title {{
      font-size: 32px;
      font-weight: 800;
      margin: 80px 0 30px;
      position: relative;
      display: inline-block;
    }}
    
    .section-title::after {{
        content: "";
        position: absolute;
        bottom: -10px;
        right: 0;
        width: 50%;
        height: 4px;
        background: var(--accent);
        border-radius: 2px;
    }}

    .feature-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 24px;
      margin-bottom: 60px;
    }}

    .feature-card {{
      padding: 30px;
      transition: all 0.4s ease;
      position: relative;
      overflow: hidden;
      opacity: 0;
    }}

    .feature-card::before {{
      content: '';
      position: absolute;
      top: 0;
      right: 0;
      width: 100%;
      height: 100%;
      background: linear-gradient(135deg, transparent 80%, var(--accent) 150%);
      opacity: 0.1;
      transition: opacity 0.4s ease;
    }}

    .feature-card:hover {{
      transform: translateY(-8px);
      box-shadow: 0 15px 35px rgba(0,0,0,0.2);
    }}

    .feature-card:hover::before {{
      opacity: 0.3;
    }}

    .card-icon {{
      width: 50px;
      height: 50px;
      border-radius: 14px;
      background: var(--accent);
      margin-bottom: 20px;
      opacity: 0.8;
      mask: url('data:image/svg+xml;utf8,<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 2L2 7L12 12L22 7L12 2Z" fill="currentColor"/><path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>') no-repeat center;
      -webkit-mask: url('data:image/svg+xml;utf8,<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 2L2 7L12 12L22 7L12 2Z" fill="currentColor"/><path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>') no-repeat center;
    }}

    .feature-card h3 {{
      margin: 0 0 12px;
      font-size: 22px;
      font-weight: 800;
    }}

    .feature-card p {{
      margin: 0;
      opacity: 0.75;
      line-height: 1.8;
    }}

    .media-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
      gap: 30px;
    }}

    .media-card {{
      padding: 0;
      overflow: hidden;
      opacity: 0;
    }}
    
    .media-card-content {{
        padding: 24px;
    }}

    .media-card h3 {{
      margin: 0 0 10px;
      font-size: 20px;
    }}

    .media-card p {{
      margin: 0;
      opacity: 0.7;
      line-height: 1.6;
    }}

    .media-wrapper {{
        width: 100%;
        background: #000;
        position: relative;
    }}

    video, audio {{
      width: 100%;
      display: block;
      outline: none;
    }}
    
    audio {{
        margin-top: 10px;
        background: transparent;
    }}

    footer {{
      margin-top: 80px;
      text-align: center;
      opacity: 0.6;
      font-size: 14px;
      padding-top: 30px;
      border-top: 1px solid var(--card-border);
    }}

    /* Animations */
    @keyframes fadeInUp {{
      from {{ transform: translateY(30px); opacity: 0; }}
      to {{ transform: translateY(0); opacity: 1; }}
    }}

    @keyframes slideDown {{
      from {{ transform: translateY(-50px); opacity: 0; }}
      to {{ transform: translateY(0); opacity: 1; }}
    }}

    .reveal {{
      animation: fadeInUp 0.8s ease-out forwards;
    }}
    
    .content-main {{ padding: 50px; animation: fadeInUp 1s ease-out forwards; }}
    .contact-form {{ display: flex; flex-direction: column; gap: 15px; margin-top: 30px; }}
    .contact-form input, .contact-form textarea {{ padding: 15px; border-radius: 8px; border: 1px solid var(--card-border); background: rgba(0,0,0,0.2); color: var(--ink); font-family: inherit; }}
    .contact-info {{ margin-top: 40px; padding-top: 30px; border-top: 1px solid var(--card-border); }}
    .contact-info p {{ opacity: 0.8; font-size: 16px; margin: 5px 0; }}
    
    .center-content {{ text-align: center; padding: 100px 40px; }}

    @media (max-width: 900px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .topbar {{ flex-direction: column; gap: 15px; padding: 20px; }}
      h1 {{ font-size: 32px; }}
      .btn-secondary {{ margin-right: 0; margin-top: 15px; display: block; text-align: center; }}
      .media-grid {{ grid-template-columns: 1fr; }}
    }}
    """.format(**palette)
    
    template = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{site_name}</title>
  <meta name="deployment-id" content="{deployment_id}" />
  <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;800&display=swap" rel="stylesheet">
  <style>{base_css}</style>
</head>
<body>
  <div class="bg-shape bg-shape-1"></div>
  <div class="bg-shape bg-shape-2"></div>

  <div class="shell">
    <header class="topbar glass">
      <a href="/" class="brand">
        <span class="brand-badge" aria-hidden="true"></span>
        <span>{site_name}</span>
      </a>
      <nav class="menu">
        <a href="/">صفحه اصلی</a>
        <a href="/about.html">درباره ما</a>
        <a href="/contact.html">ارتباط با ما</a>
      </nav>
    </header>

    {{PAGE_BODY}}

    <footer>
        <p>{footer}</p>
        <p>Deployment Identity Hash: {deployment_id}</p>
    </footer>
  </div>

  <script>
    document.addEventListener("DOMContentLoaded", () => {{
      const observer = new IntersectionObserver((entries) => {{
        entries.forEach(entry => {{
          if (entry.isIntersecting) {{
            entry.target.style.animationPlayState = 'running';
            observer.unobserve(entry.target);
          }}
        }});
      }}, {{ threshold: 0.1 }});
      
      document.querySelectorAll('.reveal').forEach(el => {{
        el.style.animationPlayState = 'paused';
        observer.observe(el);
      }});
    }});
  </script>
</body>
</html>
"""

    if page_type == "index":
        body = f"""
        <section class="hero">
          <article class="hero-main glass">
            <div class="hero-kicker">نسخه هوشمند و استقرار یافته</div>
            <h1>{site_name}</h1>
            <p>{subhead}</p>
            <div style="margin-top: 30px;">
              <a href="#services" class="btn btn-primary">مشاهده خدمات</a>
              <a href="/contact.html" class="btn btn-secondary">درخواست مشاوره رایگان</a>
            </div>
          </article>
          <aside class="hero-side glass">
            <div class="mini-stat">
                <strong>۹۸٪</strong>
                <span>آپتایم و پایداری سرویس‌ها در شبکه یکپارچه کشوری</span>
            </div>
            <div class="mini-stat">
                <strong>۲۴/۷</strong>
                <span>پشتیبانی فنی مداوم و پایش زنده عملکرد سیستم‌ها</span>
            </div>
            <div class="mini-stat">
                <strong>+۱۲۰۰</strong>
                <span>کاربر فعال روزانه بر بستر پلتفرم هوشمند اختصاصی</span>
            </div>
          </aside>
        </section>

        <h2 id="services" class="section-title">ویژگی‌های برجسته پلتفرم</h2>
        <section class="feature-grid">
          {card_markup}
        </section>

        <h2 id="multimedia" class="section-title">رسانه و چندرسانه‌ای</h2>
        <section class="media-grid">
          <article class="media-card glass reveal" style="animation-delay: 0.2s">
            <div class="media-wrapper">
              <video controls preload="metadata" poster="https://via.placeholder.com/800x450/111/444?text=Video+Preview">
                <source src="{ctx['video_sources'][0]}" type="video/mp4" />
              </video>
            </div>
            <div class="media-card-content">
                <h3>رویدادهای اخیـر</h3>
                <p>خلاصه‌ای از مهم‌ترین گردهمایی‌ها و افتخارات کسب‌شده.</p>
            </div>
          </article>

          <article class="media-card glass reveal" style="animation-delay: 0.4s">
            <div class="media-wrapper">
              <video controls preload="metadata" poster="https://via.placeholder.com/800x450/222/666?text=Promo+Video">
                <source src="{ctx['video_sources'][1]}" type="video/mp4" />
              </video>
            </div>
            <div class="media-card-content">
                <h3>تیزر تبلیغاتی محصولات</h3>
                <p>نمایشی از جدیدترین دستاوردها و سیستم‌های بهینه‌سازی شده.</p>
                <audio controls preload="none">
                  <source src="{ctx['audio_source']}" type="audio/mpeg" />
                </audio>
            </div>
          </article>
        </section>
        """
    elif page_type == "about":
        body = f"""
        <section class="content-main glass">
            <h1>درباره مجموعه {site_name}</h1>
            <p>ما مجموعه‌ای پیشرو در زمینه ارائه راهکارهای هوشمند و خدمات سازمانی هستیم. هدف ما ایجاد بستری امن، پایدار و قابل اعتماد برای کاربران و کسب‌وکارهای ایرانی است.</p>
            <p>تیم متخصص ما با سال‌ها تجربه، همواره در تلاش است تا با بهره‌گیری از جدیدترین فناوری‌های روز دنیا، نیازهای شما را به بهترین شکل ممکن برآورده سازد. ما به کیفیت، سرعت و پشتیبانی بی‌وقفه افتخار می‌کنیم.</p>
            <p>این سامانه در حال حاضر میزبان بیش از صدها سازمان و شرکت معتبر است و توانسته رضایت بالای مشتریان خود را با تمرکز بر امنیت و نوآوری جلب نماید.</p>
        </section>
        """
    elif page_type == "contact":
        body = f"""
        <section class="content-main glass">
            <h1>ارتباط با ما</h1>
            <p>برای دریافت مشاوره رایگان، ارسال پیشنهادات و یا در صورت بروز هرگونه مشکل، می‌توانید از طریق فرم زیر یا راه‌های ارتباطی با ما در تماس باشید.</p>
            
            <form class="contact-form" onsubmit="event.preventDefault(); alert('پیام شما با موفقیت ثبت شد و در اسرع وقت بررسی می‌گردد.');">
                <input type="text" placeholder="نام و نام خانوادگی" required>
                <input type="email" placeholder="ایمیل یا شماره تماس" required>
                <textarea placeholder="متن پیام شما..." rows="5" required></textarea>
                <button type="submit" class="btn btn-primary">ارسال پیام</button>
            </form>
            
            <div class="contact-info">
                <h3>اطلاعات تماس</h3>
                <p><strong>تلفن:</strong> {phone}</p>
                <p><strong>ایمیل سازمانی:</strong> {email}</p>
                <p><strong>آدرس دفتر مرکزی:</strong> {address}</p>
            </div>
        </section>
        """
    elif page_type == "404":
        body = f"""
        <section class="content-main glass center-content">
            <h1 style="font-size: 100px; margin-bottom: 0;">۴۰۴</h1>
            <h2 style="margin-top: 10px;">صفحه مورد نظر یافت نشد (Not Found)</h2>
            <p>متاسفانه صفحه‌ای که به دنبال آن هستید وجود ندارد، جابه‌جا شده است یا شما دسترسی لازم برای مشاهده آن را ندارید.</p>
            <p>لطفا از صحت آدرس وارد شده اطمینان حاصل کنید.</p>
            <div style="margin-top: 40px;">
                <a href="/" class="btn btn-primary">بازگشت به صفحه اصلی</a>
            </div>
        </section>
        """
    else:
        body = ""

    return template.replace("{PAGE_BODY}", body)


def main():
    parser = argparse.ArgumentParser(description="Generate a randomized Persian camouflage site manifest")
    parser.add_argument("--deployment-id", default="")
    parser.add_argument("--site-name", default="")
    args = parser.parse_args()
    
    deployment_id = args.deployment_id.strip()
    if not deployment_id:
        deployment_id = hashlib.sha256(str(random.random()).encode("utf-8")).hexdigest()[:12]
        
    manifest = build_manifest(deployment_id, site_name=args.site_name)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
