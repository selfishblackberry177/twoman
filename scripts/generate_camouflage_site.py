#!/usr/bin/env python3

import argparse
import hashlib
import html
import json
import random
import re


SLUG_PARTS = [
    "afaq",
    "arya",
    "baran",
    "darya",
    "didar",
    "faraz",
    "farda",
    "honar",
    "mahsa",
    "mehr",
    "namad",
    "negar",
    "parvaz",
    "rahyar",
    "saba",
    "sahar",
    "sepand",
    "setareh",
]

ROUTE_PARTS = [
    "arzyabi",
    "asnad",
    "didban",
    "enteshar",
    "ertebat",
    "farayand",
    "gozaresh",
    "hamahang",
    "hamrah",
    "khedmat",
    "modiriyat",
    "negah",
    "payesh",
    "peyvand",
    "rahkar",
    "resaneh",
    "samaneh",
    "tasvir",
]

HEADLINES = [
    "باشگاه بازی و رسانه تعاملی",
    "مرکز پخش رویدادها و محتوای گیمینگ",
    "پلتفرم هماهنگی مسابقات و محتوای تصویری",
    "درگاه مدیریت رسانه برای گیمرها",
]

SUBHEADS = [
    "این سایت برای معرفی برنامه‌های گیم‌نت، پخش ویدیو، پادکست و اطلاع‌رسانی رویدادهای داخلی طراحی شده است.",
    "بازیکن‌ها و تیم اجرایی می‌توانند از همین صفحه به برنامه مسابقات، محتوای ویدیویی و بخش اطلاعیه‌ها دسترسی داشته باشند.",
    "این نسخه از صفحه اصلی با چیدمان تازه منتشر شده تا تجربه کاربران در موبایل و دسکتاپ یکپارچه‌تر باشد.",
    "هدف این صفحه ارائه یک نمای واقعی و کاربردی از خدمات روزانه گیم‌نت، همراه با محتوای چندرسانه‌ای است.",
]

CARDS = [
    ("اتاق مسابقات", "زمان‌بندی تورنمنت‌های دوستانه و ثبت نتیجه بازی‌ها در پایان هر سانس."),
    ("پخش ویدیو", "نمایش کلیپ‌های آموزشی، گلچین بازی‌ها و تریلر رویدادهای آینده."),
    ("رسانه باشگاه", "انتشار خبرهای روزانه، گزارش برنامه‌ها و اطلاعیه‌های اجرایی."),
    ("باشگاه مشتریان", "مدیریت طرح‌های عضویت، تخفیف‌ها و امتیازهای فصلی برای کاربران."),
    ("رزرو هوشمند", "رزرو آنلاین سیستم، مشاهده ظرفیت سالن و انتخاب زمان حضور."),
    ("گزارش عملکرد", "مشاهده وضعیت سرویس‌ها، شاخص کیفیت و گزارش استفاده هفتگی."),
]

FOOTERS = [
    "چیدمان این صفحه در هر استقرار متناسب با نیاز مجموعه به‌روزرسانی می‌شود.",
    "نسخه فعلی برای ارائه تجربه یکپارچه در تمام دستگاه‌ها تنظیم شده است.",
    "اطلاعات این صفحه به‌صورت دوره‌ای بازبینی و با برنامه‌های باشگاه هماهنگ می‌شود.",
]

PALETTES = [
    {"bg_a": "#f4efe6", "bg_b": "#d9e2d0", "ink": "#1d2a24", "accent": "#9d5b34", "muted": "#5b6b60"},
    {"bg_a": "#efe7da", "bg_b": "#d7dbe8", "ink": "#1f2430", "accent": "#8d4f39", "muted": "#586174"},
    {"bg_a": "#f1eadf", "bg_b": "#d6e1df", "ink": "#1d2a2a", "accent": "#9b6c2c", "muted": "#5c6662"},
    {"bg_a": "#f3ece3", "bg_b": "#e1d6cc", "ink": "#2a221d", "accent": "#8b5a3c", "muted": "#695c53"},
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


def build_manifest(deployment_id, site_name=""):
    digest = hashlib.sha256(deployment_id.encode("utf-8")).hexdigest()
    rng = random.Random(digest)
    slug_parts = choose_unique(rng, SLUG_PARTS, 2)
    site_slug = ascii_slug(slug_parts[0], slug_parts[1], digest[:4])
    route_parts = choose_unique(rng, ROUTE_PARTS, 4)
    site_root_path = "/" + site_slug
    passenger_base_path = site_root_path + "/" + ascii_slug(route_parts[0], route_parts[1])
    node_base_path = site_root_path + "/" + ascii_slug(route_parts[2], route_parts[3])
    headline = rng.choice(HEADLINES)
    subhead = rng.choice(SUBHEADS)
    footer = rng.choice(FOOTERS)
    palette = rng.choice(PALETTES)
    cards = choose_unique(rng, CARDS, 3)
    video_sources = choose_unique(rng, VIDEO_SOURCES, 2)
    audio_source = rng.choice(AUDIO_SOURCES)
    display_name = normalize_site_name(site_name) or headline
    html_body = render_html(
        deployment_id=deployment_id,
        site_slug=site_slug,
        site_name=display_name,
        subhead=subhead,
        footer=footer,
        palette=palette,
        cards=cards,
        video_sources=video_sources,
        audio_source=audio_source,
    )
    return {
        "deployment_id": deployment_id,
        "site_name": display_name,
        "site_slug": site_slug,
        "site_root_path": site_root_path,
        "site_index_relative_path": site_slug + "/index.html",
        "passenger_base_path": passenger_base_path,
        "node_base_path": node_base_path,
        "php_base_path": passenger_base_path,
        "landing_html": html_body,
    }


def render_html(
    *,
    deployment_id,
    site_slug,
    site_name,
    subhead,
    footer,
    palette,
    cards,
    video_sources,
    audio_source,
):
    card_markup = "\n".join(
        """
        <article class="feature-card">
          <h3>{title}</h3>
          <p>{body}</p>
        </article>
        """.format(title=html.escape(title), body=html.escape(body)).strip()
        for title, body in cards
    )
    return """<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{site_name}</title>
  <meta name="deployment-id" content="{deployment_id}" />
  <style>
    :root {{
      --bg-a: {bg_a};
      --bg-b: {bg_b};
      --ink: {ink};
      --accent: {accent};
      --muted: {muted};
      --panel: rgba(255, 255, 255, 0.74);
      --line: rgba(0, 0, 0, 0.08);
      --deep: rgba(17, 17, 17, 0.78);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 8% 18%, rgba(255,255,255,0.62), transparent 24%),
        radial-gradient(circle at 84% 10%, rgba(255,255,255,0.44), transparent 28%),
        linear-gradient(140deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
    }}
    .shell {{
      width: min(1160px, calc(100% - 36px));
      margin: 0 auto;
      padding: 24px 0 56px;
    }}
    .topbar {{
      background: rgba(255, 255, 255, 0.66);
      border: 1px solid var(--line);
      border-radius: 22px;
      backdrop-filter: blur(8px);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      padding: 12px 16px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 16px;
    }}
    .brand-badge {{
      width: 34px;
      height: 34px;
      border-radius: 11px;
      background: linear-gradient(135deg, var(--accent), #c88b57);
      box-shadow: 0 8px 18px rgba(0,0,0,0.15);
    }}
    .menu {{
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .menu a {{
      color: var(--deep);
      text-decoration: none;
      font-size: 14px;
      font-weight: 600;
      padding: 8px 11px;
      border-radius: 10px;
      transition: background 0.25s ease;
    }}
    .menu a:hover {{
      background: rgba(255,255,255,0.75);
    }}
    .hero {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 1.25fr 0.95fr;
      gap: 18px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 22px 48px rgba(0, 0, 0, 0.08);
      backdrop-filter: blur(10px);
    }}
    .hero-main {{
      padding: 34px;
    }}
    .hero-kicker {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.78);
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 16px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: clamp(30px, 4vw, 52px);
      line-height: 1.2;
      letter-spacing: -0.4px;
    }}
    .hero-main p {{
      margin: 0;
      color: var(--muted);
      line-height: 2;
      font-size: 16px;
    }}
    .hero-actions {{
      margin-top: 22px;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      border: 0;
      border-radius: 14px;
      padding: 11px 18px;
      font-weight: 700;
      font-size: 14px;
      cursor: default;
    }}
    .btn-primary {{
      background: linear-gradient(135deg, var(--accent), #c58953);
      color: #fff;
      box-shadow: 0 10px 24px rgba(139, 90, 60, 0.26);
    }}
    .btn-secondary {{
      background: rgba(255,255,255,0.76);
      color: var(--ink);
      border: 1px solid rgba(0,0,0,0.1);
    }}
    .hero-side {{
      padding: 24px;
      display: grid;
      gap: 10px;
      align-content: start;
    }}
    .mini-stat {{
      background: rgba(255,255,255,0.8);
      border: 1px solid rgba(0,0,0,0.07);
      border-radius: 16px;
      padding: 14px 15px;
    }}
    .mini-stat strong {{
      display: block;
      color: var(--accent);
      margin-bottom: 4px;
      font-size: 22px;
    }}
    .mini-stat span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .feature-grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
    }}
    .feature-card {{
      background: rgba(255,255,255,0.76);
      border: 1px solid rgba(0,0,0,0.07);
      border-radius: 22px;
      padding: 22px;
      min-height: 156px;
    }}
    .feature-card h3 {{
      margin: 0 0 9px;
      font-size: 19px;
    }}
    .feature-card p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.9;
      font-size: 14px;
    }}
    .media-grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .media-card {{
      padding: 18px;
      border-radius: 22px;
      background: rgba(255,255,255,0.76);
      border: 1px solid rgba(0,0,0,0.07);
    }}
    .media-card h3 {{
      margin: 0 0 10px;
      font-size: 18px;
    }}
    .media-card p {{
      margin: 0 0 10px;
      color: var(--muted);
      line-height: 1.8;
      font-size: 14px;
    }}
    video, audio {{
      width: 100%;
      border-radius: 12px;
      background: rgba(255,255,255,0.92);
      outline: none;
    }}
    .schedule {{
      margin-top: 18px;
      overflow: hidden;
      border-radius: 22px;
      border: 1px solid rgba(0,0,0,0.08);
      background: rgba(255,255,255,0.7);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      text-align: right;
      padding: 12px 14px;
      font-size: 14px;
      border-bottom: 1px solid rgba(0,0,0,0.06);
    }}
    th {{
      font-weight: 700;
      background: rgba(255,255,255,0.76);
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    footer {{
      margin-top: 16px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
      padding-bottom: 14px;
    }}
    @media (max-width: 940px) {{
      .hero, .feature-grid, .media-grid {{
        grid-template-columns: 1fr;
      }}
      .shell {{
        width: min(100% - 20px, 1160px);
        padding-top: 14px;
      }}
      .hero-main, .hero-side, .feature-card, .media-card {{
        padding: 18px;
      }}
      .menu {{
        gap: 8px;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div class="brand">
        <span class="brand-badge" aria-hidden="true"></span>
        <span>{site_name}</span>
      </div>
      <nav class="menu">
        <a href="#services">خدمات</a>
        <a href="#events">رویدادها</a>
        <a href="#media">رسانه</a>
        <a href="#contact">تماس</a>
      </nav>
    </header>

    <section class="hero">
      <article class="panel hero-main">
        <div class="hero-kicker">باشگاه بازی | مرکز رسانه | پشتیبانی آنلاین</div>
        <h1>{site_name}</h1>
        <p>{subhead}</p>
        <div class="hero-actions">
          <span class="btn btn-primary">رزرو آنلاین سانس</span>
          <span class="btn btn-secondary">برنامه هفتگی مسابقات</span>
        </div>
      </article>
      <aside class="panel hero-side">
        <div class="mini-stat"><strong>۶۴</strong><span>سیستم فعال برای بازی‌های رقابتی</span></div>
        <div class="mini-stat"><strong>۴.۸/۵</strong><span>میانگین رضایت کاربران در فصل جاری</span></div>
        <div class="mini-stat"><strong>۲۴/۷</strong><span>پایش وضعیت سرویس‌ها و پشتیبانی داخلی</span></div>
      </aside>
    </section>

    <section id="services" class="feature-grid">
      {cards}
    </section>

    <section id="events" class="schedule">
      <table>
        <thead>
          <tr>
            <th>رویداد</th>
            <th>زمان</th>
            <th>وضعیت</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>لیگ هفتگی فیفا</td>
            <td>پنج‌شنبه | ۲۰:۳۰</td>
            <td>در حال ثبت‌نام</td>
          </tr>
          <tr>
            <td>چالش تیمی کانتر</td>
            <td>جمعه | ۱۸:۰۰</td>
            <td>ظرفیت محدود</td>
          </tr>
          <tr>
            <td>نشست معرفی تجهیزات جدید</td>
            <td>شنبه | ۱۷:۰۰</td>
            <td>برگزار می‌شود</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section id="media" class="media-grid">
      <article class="panel media-card">
        <h3>ویدیوی فضای داخلی باشگاه</h3>
        <p>مروری کوتاه بر سالن بازی، تجهیزات و بخش استریم مجموعه.</p>
        <video controls preload="metadata">
          <source src="{video_a}" type="video/mp4" />
        </video>
      </article>
      <article class="panel media-card">
        <h3>خلاصه مسابقات اخیر</h3>
        <p>منتخبی از بهترین لحظه‌های تورنمنت‌های داخلی.</p>
        <video controls preload="metadata">
          <source src="{video_b}" type="video/mp4" />
        </video>
      </article>
      <article class="panel media-card">
        <h3>پادکست هفتگی باشگاه</h3>
        <p>مرور خبرها، برنامه‌ها و زمان‌بندی رویدادهای پیش‌رو.</p>
        <audio controls preload="none">
          <source src="{audio_src}" type="audio/mpeg" />
        </audio>
      </article>
      <article id="contact" class="panel media-card">
        <h3>راه‌های ارتباطی</h3>
        <p>برای رزرو گروهی، همکاری رسانه‌ای یا پشتیبانی باشگاه با واحد ارتباط تماس بگیرید.</p>
        <p>تلفن پشتیبانی: ۰۲۱-۰۰۰۰۰۰۰۰</p>
        <p>ساعت پاسخ‌گویی: هر روز ۱۰ صبح تا ۱ بامداد</p>
      </article>
    </section>

    <footer>{footer}</footer>
  </main>
</body>
</html>
""".format(
        site_name=html.escape(site_name),
        deployment_id=html.escape(deployment_id),
        subhead=html.escape(subhead),
        footer=html.escape(footer),
        cards=card_markup,
        video_a=html.escape(video_sources[0]),
        video_b=html.escape(video_sources[1]),
        audio_src=html.escape(audio_source),
        **palette,
    )


def main():
    parser = argparse.ArgumentParser(description="Generate a randomized Persian camouflage site manifest")
    parser.add_argument("--deployment-id", default="")
    parser.add_argument("--site-name", default="")
    args = parser.parse_args()
    deployment_id = args.deployment_id.strip()
    if not deployment_id:
        deployment_id = hashlib.sha256(str(random.random()).encode("utf-8")).hexdigest()[:12]
    print(json.dumps(build_manifest(deployment_id, site_name=args.site_name), ensure_ascii=False))


if __name__ == "__main__":
    main()
