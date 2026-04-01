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
        passenger_base_path=passenger_base_path,
        node_base_path=node_base_path,
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
    passenger_base_path,
    node_base_path,
    video_sources,
    audio_source,
):
    card_markup = "\n".join(
        """
        <article class="card">
          <h3>{title}</h3>
          <p>{body}</p>
        </article>
        """.format(title=html.escape(title), body=html.escape(body)).strip()
        for title, body in cards
    )
    media_markup = """
    <section class="media-wrap">
      <article class="panel media-box">
        <h3>ویدیوی معرفی محیط بازی</h3>
        <video controls preload="metadata">
          <source src="{video_a}" type="video/mp4" />
        </video>
      </article>
      <article class="panel media-box">
        <h3>گزارش ویدیویی مسابقات داخلی</h3>
        <video controls preload="metadata">
          <source src="{video_b}" type="video/mp4" />
        </video>
      </article>
      <article class="panel media-box audio-box">
        <h3>پادکست کوتاه باشگاه</h3>
        <p>آخرین وضعیت برنامه‌ها و زمان‌بندی رویدادها را در نسخه صوتی گوش کنید.</p>
        <audio controls preload="none">
          <source src="{audio_src}" type="audio/mpeg" />
        </audio>
      </article>
    </section>
    """.format(
        video_a=html.escape(video_sources[0]),
        video_b=html.escape(video_sources[1]),
        audio_src=html.escape(audio_source),
    ).strip()
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
      --panel: rgba(255, 255, 255, 0.62);
      --line: rgba(0, 0, 0, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 16% 12%, rgba(255,255,255,0.74), transparent 24%),
        radial-gradient(circle at 88% 14%, rgba(255,255,255,0.5), transparent 22%),
        linear-gradient(132deg, var(--bg-a), var(--bg-b));
      min-height: 100vh;
    }}
    .shell {{
      width: min(1100px, calc(100% - 32px));
      margin: 0 auto;
      padding: 48px 0 56px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.3fr 0.9fr;
      gap: 24px;
      align-items: stretch;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      backdrop-filter: blur(10px);
      box-shadow: 0 18px 48px rgba(0,0,0,0.08);
    }}
    .intro {{
      padding: 32px;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,0.72);
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 18px;
    }}
    h1 {{
      margin: 0 0 14px;
      font-size: clamp(30px, 4vw, 50px);
      line-height: 1.2;
    }}
    .intro p {{
      margin: 0 0 20px;
      line-height: 1.9;
      font-size: 16px;
      color: var(--muted);
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }}
    .stat {{
      padding: 16px;
      border-radius: 20px;
      background: rgba(255,255,255,0.68);
      border: 1px solid rgba(0,0,0,0.05);
    }}
    .stat strong {{
      display: block;
      font-size: 22px;
      margin-bottom: 6px;
      color: var(--accent);
    }}
    .stat span {{
      font-size: 13px;
      color: var(--muted);
    }}
    .aside {{
      position: relative;
      padding: 28px;
      overflow: hidden;
    }}
    .aside::before {{
      content: "";
      position: absolute;
      inset: 18px;
      border-radius: 24px;
      border: 1px dashed rgba(0,0,0,0.1);
    }}
    .aside .label {{
      position: relative;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 12px;
    }}
    .aside .big {{
      position: relative;
      font-size: 36px;
      line-height: 1.15;
      margin: 0 0 18px;
    }}
    .aside .chip {{
      position: relative;
      display: inline-block;
      padding: 10px 14px;
      border-radius: 18px;
      margin: 0 0 12px 8px;
      background: rgba(255,255,255,0.78);
      border: 1px solid rgba(0,0,0,0.06);
      font-size: 13px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 18px;
      margin-top: 22px;
    }}
    .card {{
      padding: 22px;
      background: rgba(255,255,255,0.72);
      border-radius: 24px;
      border: 1px solid rgba(0,0,0,0.06);
      min-height: 180px;
    }}
    .card h3 {{
      margin: 0 0 10px;
      font-size: 20px;
    }}
    .card p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.9;
      font-size: 15px;
    }}
    .media-wrap {{
      margin-top: 24px;
      display: grid;
      gap: 18px;
      grid-template-columns: 1fr 1fr;
    }}
    .media-box {{
      padding: 22px;
      border-radius: 24px;
    }}
    .media-box h3 {{
      margin: 0 0 12px;
      font-size: 20px;
    }}
    .media-box p {{
      margin: 0 0 10px;
      line-height: 1.85;
      color: var(--muted);
      font-size: 15px;
    }}
    video, audio {{
      width: 100%;
      border-radius: 14px;
      outline: none;
      background: rgba(255,255,255,0.85);
    }}
    .audio-box {{
      grid-column: 1 / -1;
    }}
    .route-wrap {{
      margin-top: 22px;
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.68);
      border: 1px solid rgba(0,0,0,0.07);
    }}
    .route-wrap h4 {{
      margin: 0 0 10px;
      font-size: 16px;
      color: var(--ink);
    }}
    .route-list {{
      margin: 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 8px;
    }}
    .route-list a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
      word-break: break-all;
    }}
    .route-list span {{
      color: var(--muted);
      margin-left: 8px;
    }}
    footer {{
      margin-top: 18px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 860px) {{
      .hero, .grid, .media-wrap {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: 1fr; }}
      .shell {{ width: min(100% - 20px, 1100px); padding-top: 24px; }}
      .intro, .aside, .card {{ padding: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="panel intro">
        <div class="eyebrow">نسخه نمای سایت | شناسه انتشار {deployment_id}</div>
        <h1>{site_name}</h1>
        <p>{subhead}</p>
        <div class="stats">
          <div class="stat"><strong>۳</strong><span>خدمت فعال: بازی، رسانه و پشتیبانی</span></div>
          <div class="stat"><strong>۲۴/۷</strong><span>دسترسی برای مشاهده محتوا و اطلاعیه‌ها</span></div>
          <div class="stat"><strong>{slug}</strong><span>شناسه نسخه فعلی این استقرار</span></div>
        </div>
        <div class="route-wrap">
          <h4>مسیرهای سرویس داخلی</h4>
          <ul class="route-list">
            <li><span>وضعیت سرویس اصلی</span><a href="{passenger_health}" target="_blank" rel="noreferrer">{passenger_health}</a></li>
            <li><span>وضعیت سرویس سریع</span><a href="{node_health}" target="_blank" rel="noreferrer">{node_health}</a></li>
          </ul>
        </div>
      </div>
      <aside class="panel aside">
        <div class="label">باشگاه آنلاین {site_name}</div>
        <h2 class="big">محتوای ویدیویی، پادکست و اطلاعیه‌های مسابقات در یک صفحه یکپارچه.</h2>
        <span class="chip">پخش ویدیو</span>
        <span class="chip">برنامه مسابقات</span>
        <span class="chip">رسانه باشگاه</span>
      </aside>
    </section>
    <section class="grid">
      {cards}
    </section>
    {media}
    <footer>{footer}</footer>
  </main>
</body>
</html>
""".format(
        site_name=html.escape(site_name),
        deployment_id=html.escape(deployment_id),
        subhead=html.escape(subhead),
        slug=html.escape(site_slug),
        footer=html.escape(footer),
        cards=card_markup,
        media=media_markup,
        passenger_health=html.escape(passenger_base_path.rstrip("/") + "/health"),
        node_health=html.escape(node_base_path.rstrip("/") + "/health"),
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
