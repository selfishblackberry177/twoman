#!/usr/bin/env python3

import argparse
import hashlib
import html
import json
import random


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
    "سامانه روایت و پایش محتوای فرهنگی",
    "مرکز هماهنگی انتشار و آرشیو دیجیتال",
    "بستر یکپارچه پایش خبر، تصویر و اسناد",
    "درگاه مدیریت محتوا و جریان اطلاعات",
]

SUBHEADS = [
    "این صفحه برای معرفی جریان‌های محتوایی، آرشیو رسانه‌ای و خدمات داخلی مجموعه استفاده می‌شود.",
    "بخش‌های مختلف این سایت برای هماهنگی انتشار، دسته‌بندی محتوا و رهگیری به‌روزرسانی‌ها طراحی شده‌اند.",
    "ساختار این درگاه به شکلی تنظیم شده که تیم تحریریه، بایگانی و فنی از یک نمای مشترک استفاده کنند.",
    "این نمای وب در هر انتشار با هویت بصری تازه به‌روزرسانی می‌شود تا با کمپین جاری هماهنگ بماند.",
]

CARDS = [
    ("آرشیو پویا", "مرتب‌سازی نسخه‌ها، نگهداری خروجی‌ها و دسترسی سریع به اقلام منتشرشده."),
    ("هماهنگی تیمی", "تعریف گردش‌کار برای بررسی، بازبینی و انتشار محتوای روزانه."),
    ("پایش لحظه‌ای", "رهگیری وضعیت کانال‌ها، دریافت‌ها و بازخوردهای اجرایی در یک نما."),
    ("گزارش‌سازی", "تولید خلاصه‌های اجرایی برای گروه‌های تحریریه، فنی و مدیریت."),
    ("کتابخانه رسانه", "مدیریت پرونده‌های تصویری، متن‌های مرجع و نسخه‌های بازنویسی‌شده."),
    ("پوشش مناسبتی", "چیدمان سریع صفحه‌ها برای مناسبت‌ها و موج‌های خبری کوتاه‌مدت."),
]

FOOTERS = [
    "به‌روزرسانی این نما به صورت دوره‌ای انجام می‌شود.",
    "چینش این صفحه متناسب با انتشار جاری بازتولید می‌شود.",
    "نسخه نمایشی فعلی بر اساس شناسه انتشار ساخته شده است.",
]

PALETTES = [
    {"bg_a": "#f4efe6", "bg_b": "#d9e2d0", "ink": "#1d2a24", "accent": "#9d5b34", "muted": "#5b6b60"},
    {"bg_a": "#efe7da", "bg_b": "#d7dbe8", "ink": "#1f2430", "accent": "#8d4f39", "muted": "#586174"},
    {"bg_a": "#f1eadf", "bg_b": "#d6e1df", "ink": "#1d2a2a", "accent": "#9b6c2c", "muted": "#5c6662"},
    {"bg_a": "#f3ece3", "bg_b": "#e1d6cc", "ink": "#2a221d", "accent": "#8b5a3c", "muted": "#695c53"},
]


def choose_unique(rng, items, count):
    pool = list(items)
    rng.shuffle(pool)
    return pool[:count]


def ascii_slug(*parts):
    return "-".join(part.strip("-") for part in parts if part).strip("-")


def build_manifest(deployment_id):
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
    html_body = render_html(
        deployment_id=deployment_id,
        site_slug=site_slug,
        headline=headline,
        subhead=subhead,
        footer=footer,
        palette=palette,
        cards=cards,
    )
    return {
        "deployment_id": deployment_id,
        "site_slug": site_slug,
        "site_root_path": site_root_path,
        "site_index_relative_path": site_slug + "/index.html",
        "passenger_base_path": passenger_base_path,
        "node_base_path": node_base_path,
        "php_base_path": passenger_base_path,
        "landing_html": html_body,
    }


def render_html(*, deployment_id, site_slug, headline, subhead, footer, palette, cards):
    card_markup = "\n".join(
        """
        <article class="card">
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
  <title>{title}</title>
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
      font-family: Tahoma, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 20% 20%, rgba(255,255,255,0.75), transparent 26%),
        radial-gradient(circle at 80% 10%, rgba(255,255,255,0.55), transparent 22%),
        linear-gradient(135deg, var(--bg-a), var(--bg-b));
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
      font-size: clamp(30px, 4vw, 52px);
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
      font-size: 42px;
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
    footer {{
      margin-top: 18px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 860px) {{
      .hero, .grid {{ grid-template-columns: 1fr; }}
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
        <h1>{title}</h1>
        <p>{subhead}</p>
        <div class="stats">
          <div class="stat"><strong>۳</strong><span>بخش فعال برای مدیریت، آرشیو و پایش</span></div>
          <div class="stat"><strong>۲۴/۷</strong><span>دسترسی پیوسته برای تیم‌های داخلی</span></div>
          <div class="stat"><strong>{slug}</strong><span>مسیر این استقرار برای انتشار جاری</span></div>
        </div>
      </div>
      <aside class="panel aside">
        <div class="label">چینش تازه برای این انتشار</div>
        <h2 class="big">نمای عمومی این شاخه با هویت بصری تازه بازسازی شده است.</h2>
        <span class="chip">آرشیو محتوایی</span>
        <span class="chip">داشبورد داخلی</span>
        <span class="chip">پایش رسانه‌ای</span>
      </aside>
    </section>
    <section class="grid">
      {cards}
    </section>
    <footer>{footer}</footer>
  </main>
</body>
</html>
""".format(
        title=html.escape(headline),
        deployment_id=html.escape(deployment_id),
        subhead=html.escape(subhead),
        slug=html.escape(site_slug),
        footer=html.escape(footer),
        cards=card_markup,
        **palette,
    )


def main():
    parser = argparse.ArgumentParser(description="Generate a randomized Persian camouflage site manifest")
    parser.add_argument("--deployment-id", default="")
    args = parser.parse_args()
    deployment_id = args.deployment_id.strip()
    if not deployment_id:
        deployment_id = hashlib.sha256(str(random.random()).encode("utf-8")).hexdigest()[:12]
    print(json.dumps(build_manifest(deployment_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
