"""Farmer-focused PDF report export."""
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from user_messages import strip_banned_text


def create_pdf(report: dict) -> bytes:
    analysis, narrative = report["analysis"], report["narrative"]
    loc, estimate, risk = analysis["location"], analysis["estimate"], analysis["risk"]
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Green", parent=styles["Title"], textColor=colors.HexColor("#285c3d")))
    styles.add(ParagraphStyle(name="GreenBody", parent=styles["BodyText"], leading=14, textColor=colors.HexColor("#455448")))
    def p(value, style="GreenBody"):
        safe = strip_banned_text(str(value)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(safe, styles[style])
    low, high = estimate["likely_range_kg_ha"]
    story = [p("GreenYield · Your field report", "Green"),
        p(f"{loc['crop']} · {loc['season']} · {loc['district']}, {loc['state']} · {estimate['area_acres']} acres"),
        Spacer(1, 5*mm), p(f"Expected yield: about {estimate['yield_kg_ha']:,.1f} kg/ha (likely between {low:,.0f} and {high:,.0f})"),
        p(f"Expected production for your field: {estimate['total_tonnes']:.3f} tonnes"),
        p(f"Field risk: {risk['status']}")]
    for driver in risk.get("drivers", []):
        story.append(p(f"{driver['title']} · {driver.get('value', '')} · {driver['explanation']}"))
    story += [Spacer(1, 4*mm), p("Field outlook", "Heading2"), p(narrative.get("summary", ""))]
    for item in narrative.get("key_findings", []): story.append(p("• " + item))
    story += [Spacer(1, 4*mm), p("Recommended actions", "Heading2")]
    for item in narrative.get("recommendations", []):
        story.append(p(f"{item['title']}: {item['action']} {item['reason']} {item['timing']}"))
    if analysis.get("check_your_inputs"):
        story += [Spacer(1, 4*mm), p("Check your inputs", "Heading2")]
        for warning in analysis["check_your_inputs"]: story.append(p("• " + warning))
    story += [Spacer(1, 4*mm), p("Your field details", "Heading2")]
    rows = [["Field detail", "Value"]]
    for item in analysis.get("input_summary", []):
        rows.append([str(item["name"]), f"{item.get('value', '')} {item.get('unit', '')}".strip()])
    table = Table([[p(c) for c in row] for row in rows], colWidths=[74*mm, 82*mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,0), colors.HexColor("#e9efe2")), ("GRID",(0,0),(-1,-1),.35,colors.HexColor("#dce4d5")), ("VALIGN",(0,0),(-1,-1),"TOP"), ("PADDING",(0,0),(-1,-1),5)]))
    story += [table, Spacer(1, 4*mm), p("Estimates can change with weather, pests and farm management. Check major decisions with a local agronomist or the nearest agriculture extension office.")]
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=17*mm, leftMargin=17*mm, topMargin=16*mm, bottomMargin=16*mm, title="GreenYield Field Report", pageCompression=0).build(story)
    return output.getvalue()
