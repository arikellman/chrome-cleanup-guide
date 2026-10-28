"""Renders app.keepers.build_report output as a PDF: a cover page with the
rules and a per-team summary, then one page per team."""
from __future__ import annotations

import datetime as dt
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import keepers

INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#9ca3af")
ACCENT = colors.HexColor("#1d4ed8")
RULE = colors.HexColor("#e5e7eb")
KEEPER_BG = colors.HexColor("#eff6ff")

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=_styles["Title"], textColor=INK, fontSize=24, leading=30, spaceAfter=6)
SUBTITLE = ParagraphStyle("st", parent=_styles["Normal"], textColor=colors.HexColor("#4b5563"), alignment=TA_CENTER)
H1 = ParagraphStyle("h1", parent=_styles["Heading1"], textColor=INK, fontSize=18, spaceAfter=2)
H2 = ParagraphStyle("h2", parent=_styles["Heading3"], textColor=INK, spaceBefore=14)
BODY = ParagraphStyle("b", parent=_styles["Normal"], textColor=INK, fontSize=10, leading=14)
META = ParagraphStyle("m", parent=BODY, textColor=colors.HexColor("#4b5563"))


def _draft_text(draft_round: int | None) -> str:
    return "Not drafted" if draft_round is None else str(draft_round)


def _keeper_text(p: dict[str, Any]) -> str:
    if p["status"] == keepers.ELIGIBLE:
        return str(p["keeper_round"])
    return "Not keepable" if p["status"] == keepers.NOT_ELIGIBLE else "Not frozen"


def _sep1_text(p: dict[str, Any]) -> str:
    if p["status"] != keepers.NOT_FROZEN:
        return "Same"
    return p["freeze_team"] or "Free agent"


def _nice_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def _cover(report, title, season_year, freeze_date, end_date):
    today = dt.date.today()
    story = [
        Spacer(1, 0.3 * inch),
        Paragraph(title, TITLE),
        Paragraph(f"Generated {today:%B} {today.day}, {today.year}", SUBTITLE),
        Spacer(1, 0.25 * inch),
        Paragraph("Rules", H2),
    ]
    rules = [
        f"<b>Frozen:</b> on the same manager's roster on {_nice_date(freeze_date)} (the active roster that day) "
        f"and on {_nice_date(end_date)}, the last day of the {season_year} season.",
        f"<b>Keeper round:</b> {season_year} draft round minus {keepers.KEEPER_ROUND_OFFSET}, "
        "regardless of who drafted the player.",
        f"<b>Latest keeper round is {keepers.LATEST_KEEPER_ROUND}:</b> undrafted players, and players "
        f"drafted after round {keepers.LATEST_KEEPER_ROUND + keepers.KEEPER_ROUND_OFFSET}, "
        f"are round-{keepers.LATEST_KEEPER_ROUND} keepers.",
        f"<b>Not keepable:</b> drafted in round {keepers.KEEPER_ROUND_OFFSET} or earlier.",
    ]
    story += [Paragraph(f"&bull; {r}", BODY) for r in rules]
    story += [Spacer(1, 6), Paragraph("Summary", H2)]

    data = [["Team", "Manager", "Roster", "Keepable", "Earliest Rd"]]
    for t in report:
        eligible = [p for p in t["players"] if p["status"] == keepers.ELIGIBLE]
        data.append(
            [
                t["team"],
                t["manager"] or "",
                str(len(t["players"])),
                str(len(eligible)),
                str(min(p["keeper_round"] for p in eligible)) if eligible else "-",
            ]
        )
    table = Table(data, colWidths=[2.7 * inch, 1.5 * inch, 0.8 * inch, 0.9 * inch, 1.0 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 10),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("ALIGN", (2, 0), (-1, -1), "CENTER"),
                ("LINEBELOW", (0, 0), (-1, 0), 1, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    return story


def _team_page(t):
    players = t["players"]
    n_eligible = sum(1 for p in players if p["status"] == keepers.ELIGIBLE)
    sub = f"{n_eligible} keepable of {len(players)} rostered"
    if t["manager"]:
        sub = f"Manager: {t['manager']} &nbsp;&middot;&nbsp; {sub}"
    story = [Paragraph(t["team"], H1), Paragraph(sub, META), Spacer(1, 10)]

    data = [["Keeper Rd", "Player", "Draft Rd", "Sep 1 Team"]]
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    prev_status = None
    for i, p in enumerate(players, start=1):
        data.append([_keeper_text(p), p["player_name"], _draft_text(p["draft_round"]), _sep1_text(p)])
        if p["status"] == keepers.ELIGIBLE:
            style += [("FONT", (0, i), (0, i), "Helvetica-Bold", 9.5), ("BACKGROUND", (0, i), (-1, i), KEEPER_BG)]
            style.append(("TEXTCOLOR", (3, i), (3, i), MUTED))
        else:
            style.append(("TEXTCOLOR", (0, i), (-1, i), MUTED))
            if p["status"] == keepers.NOT_FROZEN:
                style.append(("TEXTCOLOR", (3, i), (3, i), INK))
        if prev_status is not None and p["status"] != prev_status:
            style.append(("LINEABOVE", (0, i), (-1, i), 0.75, MUTED))
        prev_status = p["status"]
    table = Table(data, colWidths=[1.05 * inch, 2.75 * inch, 0.9 * inch, 2.3 * inch], repeatRows=1)
    table.setStyle(TableStyle(style))
    story.append(table)
    return story


def render(
    report: list[dict[str, Any]], path: str, *, title: str, season_year: int, freeze_date: str, end_date: str
) -> None:
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.75 * inch, 0.5 * inch, title)
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.75 * inch,
        title=title,
    )
    story = _cover(report, title, season_year, freeze_date, end_date)
    for t in report:
        story.append(PageBreak())
        story += _team_page(t)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
