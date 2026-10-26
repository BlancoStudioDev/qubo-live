"""Minimal Markdown renderer for PIANO_ALLENAMENTO.md.

The plan is written with a small subset of Markdown: headings, paragraphs,
unordered/ordered lists, tables, inline code, bold and links.  A full Markdown
dependency is not needed for one local page.
"""

from __future__ import annotations

import re
from html import escape


def _inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _table(lines: list[str]) -> str:
    rows: list[list[str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    head = "".join(f"<th>{_inline(cell)}</th>" for cell in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in rows[1:]
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    html: list[str] = []
    index = 0
    list_type: str | None = None
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal list_type, list_items
        if list_type and list_items:
            tag = list_type
            html.append(
                f"<{tag}>"
                + "".join(f"<li>{item}</li>" for item in list_items)
                + f"</{tag}>"
            )
        list_type = None
        list_items = []

    while index < len(lines):
        line = lines[index]

        if line.startswith("|"):
            flush_list()
            table_lines: list[str] = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            html.append(_table(table_lines))
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_list()
            level = len(heading.group(1))
            html.append(
                f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>"
            )
            index += 1
            continue

        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            if list_type != "ul":
                flush_list()
                list_type = "ul"
            list_items.append(_inline(bullet.group(1).strip()))
            index += 1
            continue

        numbered = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if numbered:
            if list_type != "ol":
                flush_list()
                list_type = "ol"
            list_items.append(_inline(numbered.group(1).strip()))
            index += 1
            continue

        if list_type and line[:1].isspace() and line.strip():
            if list_items:
                list_items[-1] += " " + _inline(line.strip())
            index += 1
            continue

        if not line.strip():
            flush_list()
            index += 1
            continue

        flush_list()
        paragraph = [line.strip()]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            if (
                not candidate.strip()
                or candidate.startswith("|")
                or re.match(r"^#{1,6}\s+", candidate)
                or re.match(r"^\s*[-*]\s+", candidate)
                or re.match(r"^\s*\d+\.\s+", candidate)
            ):
                break
            paragraph.append(candidate.strip())
            index += 1
        html.append(f"<p>{_inline(' '.join(paragraph))}</p>")

    flush_list()
    return "\n".join(html)


def render_plan_page(markdown: str) -> str:
    body = render_markdown(markdown)
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Piano allenamento · Qubo Live</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0e0f0f;
    --surface: #171918;
    --text: #f2f3ef;
    --muted: #8a918d;
    --muted-2: #5f6763;
    --line: #2a2d2b;
    --power: #ff6b35;
    --speed: #5ad1e6;
    --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    --ui: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.65 var(--ui);
  }}
  .bar {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; padding: 14px 24px; border-bottom: 1px solid var(--line);
    position: sticky; top: 0; background: color-mix(in srgb, var(--bg) 94%, transparent);
    backdrop-filter: blur(10px); z-index: 2;
  }}
  .brand {{ color: var(--power); font: 700 12px/1 var(--mono); letter-spacing: .14em; }}
  .bar a {{
    color: var(--muted); text-decoration: none; font: 600 11px/1 var(--mono);
    border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
  }}
  .bar a:hover {{ color: var(--text); border-color: var(--muted-2); }}
  .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  main {{ max-width: 920px; margin: 0 auto; padding: 34px 24px 80px; }}
  h1 {{ font-size: clamp(30px, 5vw, 46px); line-height: 1.05; letter-spacing: -.04em; margin: 0 0 10px; }}
  h2 {{ font-size: 22px; letter-spacing: -.02em; margin: 34px 0 10px; color: var(--text); }}
  h3 {{ font-size: 17px; margin: 24px 0 8px; color: var(--speed); }}
  p {{ color: #d8dcd6; margin: 10px 0; }}
  strong {{ color: var(--text); }}
  code {{ color: var(--speed); background: #1b2221; padding: 1px 5px; border-radius: 5px; font: 0.9em var(--mono); }}
  a {{ color: var(--speed); }}
  ul, ol {{ margin: 8px 0 14px; padding-left: 24px; color: #d8dcd6; }}
  li {{ margin: 4px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0 22px; font-size: 13px; }}
  th, td {{ border: 1px solid var(--line); padding: 8px 9px; text-align: left; vertical-align: top; }}
  th {{ color: var(--muted); background: var(--surface); font: 600 10px/1.2 var(--mono); text-transform: uppercase; letter-spacing: .06em; }}
  td {{ color: #d8dcd6; }}
  @media (max-width: 620px) {{
    .bar {{ align-items: flex-start; flex-direction: column; }}
    main {{ padding: 24px 16px 60px; }}
    table {{ display: block; overflow-x: auto; }}
  }}
</style>
</head>
<body>
  <header class="bar">
    <div class="brand">QUBO LIVE · PIANO</div>
    <div class="actions">
      <a href="/">Dashboard</a>
      <a href="/piano.md">Scarica .md</a>
    </div>
  </header>
  <main>
    {body}
  </main>
</body>
</html>"""
