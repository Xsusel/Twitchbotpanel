from weasyprint import HTML
from flask import render_template
import io

def generate_pdf_report(channel, stats, analysis, messages):
    """
    Generates a PDF report using WeasyPrint.
    """
    html = render_template('report.html',
                          channel=channel,
                          stats=stats,
                          analysis=analysis,
                          messages=messages)

    pdf_bytes = HTML(string=html).write_pdf()
    return pdf_bytes
