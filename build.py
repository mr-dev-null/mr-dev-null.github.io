#!/usr/bin/env python3
"""
build.py — static site builder (Dynamic Multi-Category Edition)

Usage:
    python build.py posts/pwn/1-stack-buffer-overflow.md   # Build one post
    python build.py --all                                  # Build all posts & category indexes
    python build.py --index                                # Rebuild home & category indexes only
    python build.py --new "My Title" --cat pwn             # Create a new post under a category
"""

import argparse
import json
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
POSTS_DIR = ROOT / "posts"
POSTS_INDEX = ROOT / "posts" / "index.json"

SITE_NAME = "dev null notes"
AUTHOR = "mr-dev-null"
BASE_URL = "https://mr-dev-null.github.io"

OUT_DIR = ROOT

# Start with known default categories
DEFAULT_CATEGORIES = ["pwn", "re", "programing", "mal", "web"]


def get_all_categories() -> list[str]:
    """Dynamically registers directories inside posts/ as valid categories."""
    cats = list(DEFAULT_CATEGORIES)
    if POSTS_DIR.exists():
        for item in POSTS_DIR.iterdir():
            if item.is_dir():
                name = item.name.lower().strip()
                if name not in cats:
                    cats.append(name)
    return cats


# ── Frontmatter & Helpers ──────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3 :].strip()
    meta = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            val = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        elif val.lower() == "true":
            val = True
        elif val.lower() == "false":
            val = False
        meta[key] = val
    return meta, body


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ── Markdown Parser ───────────────────────────────────────────────────────────


def md_to_html(md: str) -> str:
    lines = md.split("\n")
    output = []
    i = 0

    def inline(text: str) -> str:
        text = re.sub(
            r'!\[([^\]]*)\]\(([^\s\)]+)(?:\s+"([^"]*)")?\)',
            lambda m: (
                f'<figure><img src="{m.group(2)}" alt="{escape(m.group(1))}" loading="lazy">'
                + (
                    f"<figcaption>{escape(m.group(3))}</figcaption>"
                    if m.group(3)
                    else ""
                )
                + "</figure>"
            ),
            text,
        )
        text = re.sub(
            r"\[([^\]]+)\]\(([^\)]+)\)",
            lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
            text,
        )
        text = re.sub(
            r"\*\*(.+?)\*\*|__(.+?)__",
            lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>",
            text,
        )
        text = re.sub(
            r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)",
            lambda m: f"<em>{m.group(1) or m.group(2)}</em>",
            text,
        )
        text = re.sub(
            r"`([^`]+)`", lambda m: f"<code>{escape(m.group(1))}</code>", text
        )
        return text

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            lang_label = line[3:].strip() or "code"
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            output.append(
                f'<div class="code-wrapper">'
                f'<div class="code-header">'
                f"<span>{escape(lang_label)}</span>"
                f'<button class="copy-btn" aria-label="Copy code"></button>'
                f"</div>"
                f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>"
                f"</div>"
            )
            i += 1
            continue

        if line.startswith("> [!"):
            m = re.match(r">\s*\[!(warn|info|danger|tip)\]\s*(.*)", line, re.I)
            if m:
                ctype = m.group(1).lower()
                content = m.group(2)
                i += 1
                while i < len(lines) and lines[i].startswith("> "):
                    content += " " + lines[i][2:]
                    i += 1
                labels = {
                    "warn": ("Warning", "callout"),
                    "info": ("Info", "callout info"),
                    "danger": ("Danger", "callout danger"),
                    "tip": ("Tip", "callout tip"),
                }
                label, cls = labels.get(ctype, ("Note", "callout info"))
                output.append(
                    f'<div class="{cls}"><strong>{label}:</strong> {inline(content)}</div>'
                )
                continue

        if line.startswith("> "):
            quote_lines = []
            while i < len(lines) and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:])
                i += 1
            output.append(
                f"<blockquote><p>{inline(' '.join(quote_lines))}</p></blockquote>"
            )
            continue

        if re.match(r"^[-*_]{3,}\s*$", line):
            output.append("<hr>")
            i += 1
            continue

        hm = re.match(r"^(#{1,4})\s+(.+)", line)
        if hm:
            level = len(hm.group(1))
            text = hm.group(2).strip()
            sid = slugify(re.sub(r"[*_`]", "", text))
            output.append(f'<h{level} id="{sid}">{inline(text)}</h{level}>')
            i += 1
            continue

        if re.match(r"^[-*+]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i]):
                items.append(f"<li>{inline(lines[i][2:].strip())}</li>")
                i += 1
            output.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                text = re.sub(r"^\d+\.\s+", "", lines[i])
                items.append(f"<li>{inline(text)}</li>")
                i += 1
            output.append("<ol>" + "".join(items) + "</ol>")
            continue

        if not line.strip():
            i += 1
            continue

        para_lines = []
        while (
            i < len(lines)
            and lines[i].strip()
            and not (
                lines[i].startswith("#")
                or lines[i].startswith("```")
                or lines[i].startswith("> ")
                or re.match(r"^[-*+]\s+", lines[i])
                or re.match(r"^\d+\.\s+", lines[i])
                or re.match(r"^[-*_]{3,}\s*$", lines[i])
            )
        ):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            output.append(f"<p>{inline(' '.join(para_lines))}</p>")
        else:
            i += 1

    return "\n".join(output)


def build_toc(html_body: str) -> str:
    headings = re.findall(r'<h2 id="([^"]+)">([^<]+)</h2>', html_body)
    if len(headings) < 2:
        return ""
    items = "".join(
        f'<li><a href="#{hid}">{escape(htxt)}</a></li>' for hid, htxt in headings
    )
    return f'<div class="toc"><h4>Contents</h4><ol>{items}</ol></div>'


# ── Render Page Layout ────────────────────────────────────────────────────────


def render_article(meta: dict, body_html: str, toc_html: str) -> str:
    title = meta.get("title", "Untitled")
    description = meta.get("description", "")
    category = meta.get("category", "").lower().strip()
    date_raw = meta.get("date", "")
    reading_time = meta.get("reading_time", "")
    tags = meta.get("tags", [])
    slug = meta.get("slug", slugify(title))

    try:
        date_str = datetime.strptime(str(date_raw), "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        date_str = str(date_raw)

    tags_list = tags if isinstance(tags, list) else [tags]
    tags_str = " · ".join(f"#{t}" for t in tags_list)

    og_url = f"{BASE_URL}/{category}/{slug}.html"
    og_tags = "".join(
        f'<meta property="article:tag" content="{escape(t)}" />' for t in tags_list
    )
    kw_str = ", ".join(tags_list)

    jsonld = {
        "@context": "[https://schema.org](https://schema.org)",
        "@type": "BlogPosting",
        "headline": title,
        "description": description,
        "datePublished": str(date_raw),
        "author": {"@type": "Person", "name": AUTHOR},
        "publisher": {"@type": "Person", "name": AUTHOR},
        "url": og_url,
        "keywords": kw_str,
    }

    footer_links = f"""
            <a href="https://github.com/{AUTHOR}" style="color:var(--dim);text-decoration:none;margin-right:12px;transition:color .15s" onmouseover="this.style.color='var(--accent)'" onmouseout="this.style.color='var(--dim)'">github</a>
            <a href="/about.html" style="color:var(--dim);text-decoration:none;transition:color .15s" onmouseover="this.style.color='var(--accent)'" onmouseout="this.style.color='var(--dim)'">about</a>"""

    nav_html = """
        <nav>
            <a href="/index.html" class="logo">dev null notes</a>
            <button class="nav-toggle" aria-expanded="false" aria-label="Toggle navigation">[menu]</button>
            <div class="nav-links">
                <a href="/pwn/index.html">pwn</a>
                <a href="/re/index.html">re</a>
                <a href="/programing/index.html">programing</a>
                <a href="/mal/index.html">mal</a>
                <a href="/web/index.html">web</a>
                <a href="/index.html">home</a>
                <a href="/about.html">about</a>
            </div>
        </nav>"""

    return f"""<!doctype html>
<html lang="en" dir="ltr">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>{escape(title)} — {SITE_NAME}</title>
        <meta name="description" content="{escape(description)}" />
        <meta name="author" content="{AUTHOR}" />
        <meta name="keywords" content="{escape(kw_str)}" />

        <meta property="og:title" content="{escape(title)}" />
        <meta property="og:description" content="{escape(description)}" />
        <meta property="og:type" content="article" />
        <meta property="og:url" content="{og_url}" />
        <meta property="og:site_name" content="{SITE_NAME}" />
        <meta property="article:published_time" content="{date_raw}" />
        <meta property="article:author" content="{AUTHOR}" />
        {og_tags}

        <meta name="twitter:card" content="summary" />
        <meta name="twitter:title" content="{escape(title)}" />
        <meta name="twitter:description" content="{escape(description)}" />

        <link rel="canonical" href="{og_url}" />
        <link rel="stylesheet" href="/style.css" />

        <script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>
    </head>
    <body>
        <div id="reading-progress"></div>
        {nav_html}
        <article>
            <div class="article-header">
                <span class="category">{escape(category.upper())}</span>
                <h1>{escape(title)}</h1>
                <div class="meta">
                    <span>{date_str}</span>
                    <span>·</span>
                    <span>{reading_time} min read</span>
                    {"<span>·</span><span>" + escape(tags_str) + "</span>" if tags_str else ""}
                </div>
            </div>

            {toc_html}

            <div class="prose">
                {body_html}
            </div>
        </article>

        <footer>
            <span>&copy; <span class="copyright-year">{datetime.now().year}</span> {SITE_NAME}</span>
            <span>{footer_links}</span>
        </footer>

        <script src="/main.js"></script>
    </body>
</html>"""


# ── Index Engines (Global & Categorical) ──────────────────────────────────────


def render_article_block(art: dict) -> str:
    title = escape(art.get("title", ""))
    desc = escape(art.get("description", ""))
    slug = art.get("slug", "")
    category = art.get("category", "").lower().strip()
    date_raw = art.get("date", "")
    rt = art.get("reading_time", "")
    tags = art.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    try:
        date_str = datetime.strptime(str(date_raw), "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        date_str = str(date_raw)

    tags_str = " · ".join(f"#{t}" for t in tags)
    href = f"/{category}/{slug}.html"

    return f"""
            <article>
                <a href="{href}">{title}</a>
                <p>{desc}</p>
                <div class="meta">
                    <span>{date_str}</span>
                    <span>·</span>
                    <span>{rt} min read</span>
                    <span>·</span>
                    <span class="cat-tag">{category.upper()}</span>
                    {"<span>·</span><span>" + escape(tags_str) + "</span>" if tags_str else ""}
                </div>
            </article>"""


def generate_index_html_file(title: str, blocks_html: str) -> str:
    nav_html = """
        <nav>
            <a href="/index.html" class="logo">dev null notes</a>
            <button class="nav-toggle" aria-expanded="false" aria-label="Toggle navigation">[menu]</button>
            <div class="nav-links">
                <a href="/pwn/index.html">pwn</a>
                <a href="/re/index.html">re</a>
                <a href="/programing/index.html">programing</a>
                <a href="/mal/index.html">mal</a>
                <a href="/web/index.html">web</a>
                <a href="/index.html">home</a>
                <a href="/about.html">about</a>
            </div>
        </nav>"""

    return f"""<!doctype html>
<html lang="en" dir="ltr">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>{title} — {SITE_NAME}</title>
        <link rel="stylesheet" href="/style.css" />
    </head>
    <body>
        {nav_html}
        <main style="max-width: var(--wide); margin: 0 auto; padding: 20px;">
            <section>
                <h2>{title}</h2>
                {blocks_html if blocks_html.strip() else "<p>No notes published here yet.</p>"}
            </section>
        </main>
        <footer>
            <span>&copy; {datetime.now().year} {SITE_NAME}</span>
        </footer>
        <script src="/main.js"></script>
    </body>
</html>"""


def update_indexes(all_posts: list[dict]) -> None:
    # Forces descending order (newest dates first)
    posts = sorted(all_posts, key=lambda p: p.get("date", ""), reverse=True)
    categories = get_all_categories()

    # 1. Root Homepage Index (Shows all categories mixed - Newest first)
    root_blocks = "".join(render_article_block(art) for art in posts)
    root_index_path = OUT_DIR / "index.html"
    root_index_path.write_text(
        generate_index_html_file("Writing", root_blocks), encoding="utf-8"
    )
    print(f"  ✓ Updated Global Home → /index.html ({len(posts)} items)")

    # 2. Dynamic Category Specific Folders & Indexes (Newest first)
    for cat in categories:
        cat_posts = [p for p in posts if p.get("category", "").lower().strip() == cat]
        cat_blocks = "".join(render_article_block(art) for art in cat_posts)

        cat_dir = OUT_DIR / cat
        cat_dir.mkdir(parents=True, exist_ok=True)

        cat_idx_file = cat_dir / "index.html"
        cat_idx_file.write_text(
            generate_index_html_file(f"Notes // {cat.upper()}", cat_blocks),
            encoding="utf-8",
        )
        print(
            f"  ✓ Updated Category Index → /{cat}/index.html ({len(cat_posts)} items)"
        )


# ── Index Handling JSON ───────────────────────────────────────────────────────


def load_index() -> list[dict]:
    if POSTS_INDEX.exists():
        return json.loads(POSTS_INDEX.read_text(encoding="utf-8"))
    return []


def save_index(posts: list[dict]) -> None:
    POSTS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    POSTS_INDEX.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Index Handling JSON ───────────────────────────────────────────────────────

# ... (load_index and save_index stay the same) ...


def upsert_post(posts: list[dict], meta: dict) -> list[dict]:
    slug = meta["slug"]
    # Filter out old reference if it exists
    posts = [p for p in posts if p.get("slug") != slug]
    posts.append(
        {
            "slug": slug,
            "title": meta.get("title", ""),
            "description": meta.get("description", ""),
            "category": meta.get("category", "").lower().strip(),
            "date": str(meta.get("date", "")),
            "reading_time": meta.get("reading_time", ""),
            "tags": meta.get("tags", []),
        }
    )
    # Keep database index array strictly sorted descending (newest first)
    return sorted(posts, key=lambda p: p.get("date", ""), reverse=True)


# ── Core Build Commands ────────────────────────────────────────────────────────


def build_post(md_path: Path, all_posts: list[dict]) -> dict | None:
    print(
        f"\n→ Building {md_path.relative_to(ROOT) if md_path.is_relative_to(ROOT) else md_path.name}"
    )
    text = md_path.read_text(encoding="utf-8")
    meta, body_md = parse_frontmatter(text)

    if not meta:
        print("  [error] No frontmatter found.")
        return None

    if "category" not in meta or not meta["category"]:
        meta["category"] = (
            md_path.parent.name if md_path.parent.name != "posts" else "uncategorized"
        )

    if "title" not in meta:
        print("  [error] Missing required frontmatter field: title")
        return None
    if "slug" not in meta:
        meta["slug"] = slugify(meta["title"])

    category = meta["category"].lower().strip()
    slug = meta["slug"]

    target_dir = OUT_DIR / category
    target_dir.mkdir(parents=True, exist_ok=True)

    body_html = md_to_html(body_md)
    toc_html = build_toc(body_html)

    html = render_article(meta, body_html, toc_html)
    out_path = target_dir / f"{slug}.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"  ✓ Written → /{out_path.relative_to(OUT_DIR)}")
    return meta


def build_one(md_path: Path) -> None:
    all_posts = load_index()
    meta = build_post(md_path, all_posts)
    if meta:
        all_posts = upsert_post(all_posts, meta)
        save_index(all_posts)
        print("\n→ Updating indices…")
        update_indexes(all_posts)
        print("\n✓ Done.")


def build_all() -> None:
    if not POSTS_DIR.exists():
        print(f"[error] posts/ directory not found")
        sys.exit(1)

    md_files = sorted(POSTS_DIR.glob("**/*.md"))
    if not md_files:
        print("[info] No markdown files found inside posts/")
        return

    print(f"Found {len(md_files)} post(s)")
    all_posts = load_index()
    for md_path in md_files:
        meta = build_post(md_path, all_posts)
        if meta:
            all_posts = upsert_post(all_posts, meta)

    save_index(all_posts)
    print("\n→ Updating indices…")
    update_indexes(all_posts)
    print("\n✓ Done.")


def create_template(title: str, target_cat: str) -> None:
    slug = slugify(title)
    target_cat = target_cat.lower().strip()

    write_dir = POSTS_DIR / target_cat
    write_dir.mkdir(parents=True, exist_ok=True)

    out_path = write_dir / f"{slug}.md"
    if out_path.exists():
        print(f"[error] File already exists: {out_path}")
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d")
    out_path.write_text(
        f"""---
title: {title}
description: A short description of this post.
category: {target_cat}
date: {today}
reading_time: 5
tags: [writeup, ctf]
slug: {slug}
---

## Introduction

Write your introduction here.
""",
        encoding="utf-8",
    )
    print(f"✓ Created Template: {out_path}")
    print(f"  To compile run: python build.py {out_path}")


# ── CLI Entrypoint ────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=f"{SITE_NAME} — static site builder")
    parser.add_argument("file", nargs="?", help="Path to .md file to build")
    parser.add_argument("--all", action="store_true", help="Build all posts & indexes")
    parser.add_argument(
        "--index", action="store_true", help="Rebuild index engines only"
    )
    parser.add_argument("--new", metavar="TITLE", help="Create a new post template")
    parser.add_argument(
        "--cat", metavar="CATEGORY", default="pwn", help="Category folder target"
    )

    args = parser.parse_args()

    if args.new:
        create_template(args.new, args.cat)
    elif args.all:
        build_all()
    elif args.index:
        update_indexes(load_index())
    elif args.file:
        md_path = Path(args.file)
        if not md_path.exists():
            print(f"[error] File not found: {md_path}")
            sys.exit(1)
        build_one(md_path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
