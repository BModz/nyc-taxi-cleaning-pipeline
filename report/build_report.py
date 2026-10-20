"""Build report/Report.pdf from the captured terminal output in evidence/.

Usage:
    python report/build_report.py --name "Your Name" --id "Student ID"

Each evidence/*.txt file holds a command and its real output. They are drawn
as terminal screenshots, placed into report.html, and printed to PDF with
headless Microsoft Edge.
"""

import argparse
import html
import subprocess
import textwrap
from string import Template
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
OUT = ROOT / "report"
IMG = OUT / "img"
REPO_URL = "https://github.com/BModz/nyc-taxi-cleaning-pipeline"
REPORT_DATE = "20 October 2026"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

FONT = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 15)
FONT_B = ImageFont.truetype(r"C:\Windows\Fonts\consolab.ttf", 15)
BG, FG, DIM = (12, 12, 12), (204, 204, 204), (118, 118, 118)
PROMPT, ERR, OK = (97, 214, 214), (231, 72, 86), (22, 198, 12)
WRAP = 118


def lines_of(name: str) -> list[str]:
    return (EVIDENCE / f"{name}.txt").read_text(encoding="utf-8").splitlines()


def block(name: str, start: str, end: str | None = None) -> list[str]:
    """Lines from `start` marker up to (not including) the `end` marker."""
    lines = lines_of(name)
    i = next(n for n, l in enumerate(lines) if l.startswith(start))
    j = next((n for n, l in enumerate(lines[i + 1:], i + 1) if end and l.startswith(end)), len(lines))
    return [lines[0], *lines[i:j]]


def trim(lines: list[str], head: int, tail: int) -> list[str]:
    if len(lines) <= head + tail + 1:
        return lines
    omitted = len(lines) - head - tail
    return lines[:head] + [f"\0... {omitted} lines of output omitted ..."] + lines[-tail:]


def render(lines: list[str], filename: str, title: str = "Windows PowerShell") -> str:
    wrapped: list[str] = []
    for line in lines:
        line = line.replace("\t", "    ")
        wrapped += textwrap.wrap(line, WRAP, drop_whitespace=False, replace_whitespace=False) or [""]
    lh, pad, bar = 19, 14, 30
    width = max(760, pad * 2 + 8 * max(len(l) for l in wrapped) + 10)
    height = bar + pad * 2 + lh * len(wrapped)
    im = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, width, bar], fill=(32, 32, 32))
    d.text((12, 7), title, font=FONT, fill=(220, 220, 220))
    for k, x in enumerate((width - 96, width - 64, width - 32)):
        d.text((x + 6, 6), "–□×"[k], font=FONT, fill=(200, 200, 200))
    y = bar + pad
    for line in wrapped:
        color, font = FG, FONT
        if line.startswith("PS "):
            color, font = PROMPT, FONT_B
        elif line.startswith("\0"):
            color, line = DIM, line[1:]
        elif any(w in line for w in ("Error", "failed", "refused", "cannot find")):
            color = ERR
        elif line.startswith(("Verified", "Rows read")) or line.endswith("Healthy "):
            color = OK
        d.text((pad, y), line, font=font, fill=color)
        y += lh
    IMG.mkdir(parents=True, exist_ok=True)
    im.save(IMG / filename)
    return f"img/{filename}"


def shot(src: str, caption: str) -> str:
    return f'<figure><img src="{src}"><figcaption>{html.escape(caption)}</figcaption></figure>'


FLOW_SVG = """
<svg viewBox="0 0 900 190" xmlns="http://www.w3.org/2000/svg" font-family="Calibri, Arial" font-size="13">
  <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
    <path d="M0,0 L10,5 L0,10 z" fill="#000"/></marker></defs>
  <g fill="#fff" stroke="#000" stroke-width="1">
    <rect x="10" y="20" width="150" height="70" rx="0"/><rect x="190" y="20" width="150" height="70" rx="0"/>
    <rect x="370" y="20" width="160" height="70" rx="0"/><rect x="560" y="20" width="150" height="70" rx="0"/>
    <rect x="740" y="20" width="150" height="70" rx="0"/>
  </g>
  <g fill="#000" text-anchor="middle" font-size="15">
    <text x="85" y="48">Source</text><text x="265" y="48">Extract</text><text x="450" y="48">Transform</text>
    <text x="635" y="48">Load</text><text x="815" y="48">Serve</text>
  </g>
  <g fill="#333" text-anchor="middle">
    <text x="85" y="70">TLC CSV.gz on GitHub</text><text x="265" y="70">download + read_csv</text>
    <text x="450" y="70">dtypes, dates, clean()</text><text x="635" y="70">to_sql → Postgres</text>
    <text x="815" y="70">psql / pgAdmin</text>
  </g>
  <g stroke="#000" stroke-width="2" marker-end="url(#a)">
    <line x1="160" y1="55" x2="188" y2="55"/><line x1="340" y1="55" x2="368" y2="55"/>
    <line x1="530" y1="55" x2="558" y2="55"/><line x1="710" y1="55" x2="738" y2="55"/>
  </g>
  <rect x="190" y="115" width="520" height="60" rx="0" fill="none" stroke="#000" stroke-dasharray="5 4"/>
  <text x="200" y="134" fill="#222">taxi_ingest:v001 container (Dockerfile → ingest_data.py), 100,000-row chunks</text>
  <text x="200" y="154" fill="#222">network pg-network (docker-compose.yaml) → host "pgdatabase", table yellow_taxi_trips</text>
  <rect x="740" y="115" width="150" height="60" rx="0" fill="none" stroke="#000" stroke-dasharray="5 4"/>
  <text x="815" y="134" fill="#222" text-anchor="middle">pgAdmin :8085</text>
  <text x="815" y="154" fill="#222" text-anchor="middle">Postgres :5432</text>
</svg>
"""


def build(name: str, student_id: str) -> Path:
    s = {}
    s["up"] = render(lines_of("03a_compose_up"), "03a_compose_up.png")
    s["ps"] = render(lines_of("03b_compose_ps"), "03b_compose_ps.png")
    s["build"] = render(trim(lines_of("03c_docker_build"), 4, 12), "03c_docker_build.png")
    s["run"] = render(lines_of("03d_docker_run_ingest"), "03d_docker_run.png")
    s["count"] = render(lines_of("03e_count"), "03e_count.png")
    s["schema"] = render(lines_of("03f_schema"), "03f_schema.png")
    s["raw"] = render(block("04_pipeline_run", "=== Raw data profile", "=== Cleaned"), "04a_raw_profile.png")
    s["odd"] = render(lines_of("04b_sql_odd_values"), "04b_sql_odd_values.png")
    s["rules"] = render(block("04_pipeline_run", "=== {rule: rows_removed}", "Cleaned data:"), "05a_rules.png")
    s["metrics"] = render(block("04_pipeline_run", "=== Cleaning metrics", "=== {rule"), "05b_metrics.png")
    s["clean"] = render(block("04_pipeline_run", "=== Cleaned data profile", "=== Cleaning"), "05c_clean_profile.png")
    s["sql"] = render(lines_of("06_sql_crosscheck"), "06_sql_crosscheck.png")
    err = lines_of("07c_localhost_error")
    s["err"] = render([err[0], "\0... Python traceback omitted ...", *err[-6:]], "07a_localhost_error.png")
    fix = lines_of("03d_docker_run_ingest")
    s["fix"] = render([fix[0], *fix[1:4], "\0... chunks 2-12 omitted ...", *fix[-2:]], "07b_fix.png")
    s["daemon"] = render(lines_of("07a_docker_not_running") + lines_of("07b_docker_running"), "07c_daemon.png")

    page = TEMPLATE.substitute(
        name=html.escape(name), sid=html.escape(student_id), date=REPORT_DATE,
        repo=REPO_URL, flow=FLOW_SVG,
        **{k: shot(v, c) for k, (v, c) in {
            "up": (s["up"], "docker compose up -d --wait: network, Postgres and pgAdmin created and healthy"),
            "ps": (s["ps"], "docker compose ps: both services running with their ports"),
            "build": (s["build"], "docker build -t taxi_ingest:v001 . (pip install lines trimmed)"),
            "run": (s["run"], "docker run ... --host=pgdatabase: 14 chunks inserted, 1,369,765 rows"),
            "count": (s["count"], "SELECT COUNT(*) FROM yellow_taxi_trips = 1,369,765"),
            "schema": (s["schema"], "\\d yellow_taxi_trips: Int64 columns became bigint, dates became timestamp"),
            "raw": (s["raw"], "print(df) of the raw profile: dtype, nulls, min, max, mean per column"),
            "odd": (s["odd"], "Raw counts of odd values and exact duplicates, queried in Postgres"),
            "rules": (s["rules"], "{rule: rows_removed} and rows before/after each rule"),
            "metrics": (s["metrics"], "All cleaning metrics, including filled and flagged rows"),
            "clean": (s["clean"], "print(df) of the cleaned profile: every range is now inside its rule"),
            "sql": (s["sql"], "Independent SQL cross-check of two pandas numbers"),
            "err": (s["err"], "The error: --host=localhost from inside the container"),
            "fix": (s["fix"], "The fix: --host=pgdatabase, the compose service name"),
            "daemon": (s["daemon"], "A smaller error: Docker Desktop was not running, then fixed by starting it"),
        }.items()},
    )
    html_path = OUT / "report.html"
    html_path.write_text(page, encoding="utf-8")
    pdf = OUT / "Report.pdf"
    subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf}", html_path.as_uri()],
        check=True, capture_output=True,
    )
    return pdf


TEMPLATE = Template((Path(__file__).parent / "template.html").read_text(encoding="utf-8"))

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="[Your Name]")
    p.add_argument("--id", default="[Student ID]")
    a = p.parse_args()
    print(build(a.name, a.id))
