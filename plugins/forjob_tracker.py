# plugins/forjob_tracker.py
"""Create ForJob Excel tracker workbook on Desktop/ForJob/Tracker/."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from tools.base import BaseTool


class ForJobCreateTrackerTool(BaseTool):
    @property
    def name(self) -> str:
        return "forjob_create_tracker"

    @property
    def description(self) -> str:
        return (
            "Create the ForJob_Master_Job_Tracker.xlsx workbook under "
            "Desktop/ForJob/Tracker/ with Dashboard, Job Applications, Read Me, "
            "Interview Preparation, and Follow-ups sheets. "
            "Use when user asks to create the ForJob Excel job tracker."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional full output path. Default: Desktop/ForJob/Tracker/ForJob_Master_Job_Tracker.xlsx",
                }
            },
            "required": [],
        }

    def execute(self, path: str = "", **kwargs) -> Dict[str, Any]:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.worksheet.datavalidation import DataValidation
        except ImportError:
            return {
                "content": "openpyxl not installed. Run: pip install openpyxl",
                "is_error": True,
            }

        try:
            home = Path(os.path.expanduser("~"))
            default = home / "Desktop" / "ForJob" / "Tracker" / "ForJob_Master_Job_Tracker.xlsx"
            out = Path(path) if path else default
            out.parent.mkdir(parents=True, exist_ok=True)

            wb = Workbook()
            header_fill = PatternFill("solid", fgColor="1F4E79")
            header_font = Font(bold=True, color="FFFFFF", size=11)
            title_font = Font(bold=True, size=14, color="1F4E79")
            thin = Border(
                left=Side(style="thin", color="BFBFBF"),
                right=Side(style="thin", color="BFBFBF"),
                top=Side(style="thin", color="BFBFBF"),
                bottom=Side(style="thin", color="BFBFBF"),
            )
            wrap = Alignment(wrap_text=True, vertical="top")

            def style_header(ws, row, cols):
                for c in range(1, cols + 1):
                    cell = ws.cell(row=row, column=c)
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(
                        wrap_text=True, vertical="center", horizontal="center"
                    )
                    cell.border = thin

            # Dashboard
            ws = wb.active
            ws.title = "Dashboard"
            ws["A1"] = "FORJOB – JOB SEARCH DASHBOARD"
            ws["A1"].font = title_font
            ws.merge_cells("A1:C1")
            ws["A3"] = "Metric"
            ws["B3"] = "Value"
            ws["C3"] = "Definition"
            style_header(ws, 3, 3)
            metrics = [
                ("Jobs Discovered", "Rows with a company name"),
                ("Shortlisted", "Currently shortlisted"),
                ("Awaiting Approval", "Prepared but not submitted"),
                ("Verified Applications", "Applied with verified confirmation"),
                ("Interviews Scheduled", "Upcoming interviews"),
                ("Offers Received", "Offers recorded"),
                ("Rejected", "Rejected applications"),
                ("No Response", "No response status"),
            ]
            for i, (m, d) in enumerate(metrics, start=4):
                ws.cell(row=i, column=1, value=m).border = thin
                ws.cell(row=i, column=2, value="").border = thin
                ws.cell(row=i, column=3, value=d).border = thin
            ws.column_dimensions["A"].width = 24
            ws.column_dimensions["B"].width = 12
            ws.column_dimensions["C"].width = 40

            # Job Applications
            ws2 = wb.create_sheet("Job Applications")
            headers = [
                "Job ID", "Company", "Job Title", "Country", "City / Location",
                "Job Description", "Original Job URL", "Match Score (%)", "Salary / Range",
                "Currency", "Work Mode", "Visa Sponsorship", "CV Version", "Application Type",
                "Date Discovered", "Date Applied", "Status", "Submission Verified?",
                "Confirmation / Reference", "Recruiter Name", "Recruiter Email",
                "Follow-up Date", "Interview Date / Time", "Interview Stage",
                "Interview Preparation Link / Notes", "Next Action", "Notes",
            ]
            ws2.append(headers)
            style_header(ws2, 1, len(headers))
            samples = [
                [
                    "JOB-001", "TMC Middle East",
                    "Solution Architect – Cards & Digital Payments (Pre-Sales / Client Solutioning)",
                    "UAE", "Dubai (Remote / WFH)",
                    "Client-facing cards/payments solutioning; BRDs, DOUs, SOWs.",
                    "https://ae.linkedin.com/jobs/view/solution-architect-cards-digital-payments-tmc-middle-east",
                    62, "", "", "Remote", "To verify", "Payments & Banking Architect", "Other",
                    "2026-11-05", "", "Discovered", "No", "", "", "", "", "", "",
                    "", "Verify mandatory domain requirements before applying",
                    "Not applied.",
                ],
                [
                    "JOB-002", "Unlock",
                    "Solutions Architect (Cards & Payments)",
                    "UAE", "Dubai",
                    "Cards issuing/acquiring architecture; platform migration.",
                    "https://zerotaxjobs.com/jobs/solutions-architect-cards-payments",
                    72, "", "", "On-site", "To verify", "Payments & Banking Architect", "Other",
                    "2026-11-05", "", "Discovered", "No", "", "", "", "", "", "",
                    "", "Verify employer vacancy and visa",
                    "Not applied.",
                ],
            ]
            for row in samples:
                ws2.append(row)

            for formula1, col in [
                ('"Discovered,Shortlisted,CV Prepared,Awaiting Approval,Applied,Assessment,Interview Scheduled,Interview Completed,Offer Received,Rejected,Withdrawn,No Response,Position Closed"', "Q"),
                ('"Yes,No"', "R"),
                ('"Easy Apply,External,Other,Email,Referral"', "N"),
                ('"UAE,Europe,Pakistan,Remote,Other"', "D"),
            ]:
                dv = DataValidation(type="list", formula1=formula1, allow_blank=True)
                ws2.add_data_validation(dv)
                dv.add(f"{col}2:{col}500")

            ws2.freeze_panes = "A2"
            ws2.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"
            for i in range(1, len(headers) + 1):
                ws2.column_dimensions[get_column_letter(i)].width = 14
            ws2.column_dimensions["F"].width = 40
            ws2.column_dimensions["G"].width = 28

            # Read Me
            ws3 = wb.create_sheet("Read Me")
            ws3["A1"] = "FORJOB – MASTER JOB APPLICATION TRACKER"
            ws3["A1"].font = title_font
            ws3["A3"] = "Topic"
            ws3["B3"] = "Guidance"
            style_header(ws3, 3, 2)
            guidance = [
                ("Purpose", "Central record for job discovery, applications, follow-ups and interview preparation."),
                ("Important rule", "Never set Status = Applied unless Submission Verified? = Yes."),
                ("Match Score", "Technical 30, experience 25, architecture 20, location/eligibility 15, seniority/compensation 10."),
                ("Workflow", "Discovered → Shortlisted → CV Prepared → Awaiting Approval → Applied → Interview → Offer."),
                ("Security", "Do not store passwords, OTPs, cookies, tokens, or passport numbers."),
            ]
            for i, (a, b) in enumerate(guidance, start=4):
                ws3.cell(row=i, column=1, value=a).border = thin
                cell = ws3.cell(row=i, column=2, value=b)
                cell.border = thin
                cell.alignment = wrap
            ws3.column_dimensions["A"].width = 18
            ws3.column_dimensions["B"].width = 80

            # Interview Preparation
            ws4 = wb.create_sheet("Interview Preparation")
            ih = [
                "Job ID", "Company", "Job Title", "Interview Date / Time", "Interview Stage",
                "Interviewer / Role", "JD Must-Haves", "Technical Topics", "System Design Topics",
                "Behavioral Questions", "STAR Examples to Prepare", "Company Research",
                "Questions to Ask Employer", "Preparation Status", "Feedback / Lessons", "Next Step",
            ]
            ws4.append(ih)
            style_header(ws4, 1, len(ih))
            ws4.freeze_panes = "A2"
            for i in range(1, len(ih) + 1):
                ws4.column_dimensions[get_column_letter(i)].width = 16

            # Follow-ups
            ws5 = wb.create_sheet("Follow-ups")
            fh = [
                "Job ID", "Company", "Job Title", "Contact Name", "Contact Email",
                "Communication Date", "Channel", "Summary", "Response / Outcome",
                "Next Follow-up Date", "Next Action", "Status",
            ]
            ws5.append(fh)
            style_header(ws5, 1, len(fh))
            ws5.freeze_panes = "A2"
            for i in range(1, len(fh) + 1):
                ws5.column_dimensions[get_column_letter(i)].width = 16

            wb.save(out)
            return {
                "content": f"Created ForJob tracker: {out}",
                "is_error": False,
            }
        except Exception as e:
            return {"content": f"Failed to create tracker: {e}", "is_error": True}
