# agent/outcome_compiler.py
"""
Phase 3 — Outcome-goal compiler (local, no LLM).

Turns common natural-language goals into structured plans that the
existing PlanExecutor can run (same shape as Planner output).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def _extract_document_path(text: str) -> str:
    """Pull a local document path from free-form user text (any wording)."""
    if not text:
        return ""
    g = text.strip()
    ext = r"docx|pdf|txt|md|csv|json|log"
    # 1) Windows absolute C:\... or C:/...
    m = re.search(rf"((?:[A-Za-z]:\\|\\)[^\s\"']+\.(?:{ext}))", g)
    if not m:
        m = re.search(rf"((?:[A-Za-z]:/)[^\s\"']+\.(?:{ext}))", g)
    # 2) Desktop/Documents/Downloads relative
    if not m:
        m = re.search(
            rf"((?:Desktop|Documents|Downloads)[\\/][^\s\"']+\.(?:{ext}))",
            g,
            re.I,
        )
    # 3) Any path-like token with supported extension
    if not m:
        m = re.search(rf"([^\s\"']+\.(?:{ext}))", g, re.I)
    if not m:
        return ""
    path = m.group(1).strip().strip('"').strip("'")
    path = re.sub(
        r"^(?:kept\s+at|located\s+at|saved\s+at|stored\s+at|from|at|in|on)\s+",
        "",
        path,
        flags=re.I,
    ).strip().strip('"').strip("'")
    return path



from typing import Any, Dict, List, Optional
from agent.skill_author import load_all_custom_manifests


def _desktop(*parts: str) -> str:
    home = Path.home()
    return str((home / "Desktop").joinpath(*parts)).replace("\\", "/")


def _documents(*parts: str) -> str:
    home = Path.home()
    return str((home / "Documents").joinpath(*parts)).replace("\\", "/")


class OutcomeCompiler:
    """
    Compile outcome-style goals into planner plans.
    Returns None if no template matches (caller falls back to Planner).
    """

    def compile(self, goal: str) -> Optional[Dict[str, Any]]:
        g = (goal or "").strip()
        if not g:
            return None
        low = g.lower()

        # Custom skills (authored packs) — trigger phrase match
        try:
            for man in load_all_custom_manifests():
                if man.get("enabled") is False:
                    continue
                triggers = [str(x).strip().lower() for x in (man.get("triggers") or []) if x]
                if any(tr and (tr == low or tr in low) for tr in triggers):
                    steps = man.get("steps") or []
                    if steps:
                        return {
                            "goal": g,
                            "needs_confirmation": any(
                                (s.get("tool") in ("write_developer_code", "office_new"))
                                for s in steps
                            ),
                            "source": "custom_skill",
                            "skill_id": man.get("skill_id"),
                            "steps": steps,
                        }
        except Exception:
            pass

        # 1) Create note / memo on desktop
        m = re.search(
            r"(?:create|make|write)\s+(?:a\s+)?(?:note|memo|reminder)\s+"
            r"(?:(?:named|called)\s+)?([a-zA-Z0-9_.-]+\.(?:txt|md))?\s*"
            r"(?:on\s+(?:the\s+)?desktop)?\s*"
            r"(?:with\s+(?:text|content)\s+)?[:\-]?\s*(.+)$",
            g,
            re.I,
        )
        if m and ("note" in low or "memo" in low or "reminder" in low):
            fname = (m.group(1) or "note.txt").strip()
            content = (m.group(2) or "").strip() or "Note"
            path = _desktop(fname)
            return self._file_plan(g, path, content + "\n", verify_text=content)

        # 2) Project starter: folder + readme on desktop
        m = re.search(
            r"(?:start|create|set\s*up)\s+(?:a\s+)?project\s+(?:named|called)?\s*([a-zA-Z0-9_.-]+)"
            r"(?:\s+with\s+readme)?",
            g,
            re.I,
        )
        if not m:
            m = re.search(r"project\s+starter\s+([a-zA-Z0-9_.-]+)", g, re.I)
        if m:
            name = m.group(1)
            folder = _desktop(name)
            readme = f"{folder}/README.md"
            return {
                "goal": g,
                "needs_confirmation": True,
                "source": "outcome_compiler",
                "steps": [
                    {
                        "id": 1,
                        "title": f"Create project folder {name}",
                        "tool": "write_developer_code",
                        "arguments": {
                            "filepath": f"{folder}/.keep",
                            "content": "",
                        },
                        "risk": "low",
                        "success_check": {"type": "file_exists", "path": f"{folder}/.keep"},
                    },
                    {
                        "id": 2,
                        "title": f"Create README for {name}",
                        "tool": "write_developer_code",
                        "arguments": {
                            "filepath": readme,
                            "content": f"# {name}\n\nProject started by VoiceAssistant.\n",
                        },
                        "risk": "low",
                        "success_check": {
                            "type": "file_contains",
                            "path": readme,
                            "text": name,
                        },
                    },
                ],
            }

        # 3) Daily log line append/create
        m = re.search(
            r"(?:add|append|log)\s+(?:to\s+)?(?:daily\s+)?log\s*[:\-]?\s*(.+)$",
            g,
            re.I,
        )
        if m:
            line = m.group(1).strip()
            path = _desktop("daily_log.txt")
            content = line + "\n"
            if os.path.isfile(path):
                try:
                    prev = Path(path).read_text(encoding="utf-8", errors="ignore")
                    content = prev.rstrip("\n") + "\n" + line + "\n"
                except Exception:
                    pass
            return self._file_plan(g, path, content, verify_text=line, title="Update daily log")


        # ForJob / multi-folder create on desktop
        if re.search(r"create\s+folder\s+forjob|forjob.*subfolders|folder\s+forjob\s+on\s+desktop", low):
            base = "Desktop/ForJob"
            subs = ["Profile", "CV_Versions", "Job_Inbox", "Applications", "Tracker", "Interview_Prep"]
            # Also match explicit list in user text
            m = re.search(r"subfolders?\s+([a-zA-Z0-9_,\s]+)", g, re.I)
            if m:
                parts = re.split(r"[, ]+", m.group(1).strip())
                parts = [x for x in parts if x and x.lower() not in ("and", "with")]
                if parts:
                    subs = parts
            steps = [{
                "id": 1,
                "title": "Create ForJob root",
                "tool": "write_developer_code",
                "arguments": {"filepath": base + "/.keep", "content": "ForJob workspace"},
                "risk": "low",
                "success_check": {"type": "file_exists", "path": base + "/.keep"},
            }]
            for i, name in enumerate(subs, start=2):
                steps.append({
                    "id": i,
                    "title": f"Create {name}",
                    "tool": "write_developer_code",
                    "arguments": {
                        "filepath": f"{base}/{name}/.keep",
                        "content": name,
                    },
                    "risk": "low",
                    "success_check": {"type": "file_exists", "path": f"{base}/{name}/.keep"},
                })
            return {
                "goal": g,
                "needs_confirmation": True,
                "source": "outcome_compiler",
                "steps": steps,
            }

        # Generic: create folder X on desktop
        m = re.search(
            r"create\s+(?:a\s+)?folder\s+(?:named\s+)?([a-zA-Z0-9_\-]+)(?:\s+on\s+desktop)?",
            g,
            re.I,
        )
        if m and "subfolder" not in low:
            name = m.group(1)
            path = f"Desktop/{name}/.keep"
            return {
                "goal": g,
                "needs_confirmation": True,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Create folder {name}",
                    "tool": "write_developer_code",
                    "arguments": {"filepath": path, "content": name},
                    "risk": "low",
                    "success_check": {"type": "file_exists", "path": path},
                }],
            }

        # --- Skill pack outcomes (Browser / Office / VS Code) ---
        # Browser: open URL
        m = re.search(
            r"(?:open|go\s+to|browse)\s+(https?://\S+|www\.\S+)",
            g,
            re.I,
        )
        if m:
            url = m.group(1).strip().rstrip(".")
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Open URL {url}",
                    "tool": "browser_open",
                    "arguments": {"url": url},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }


        # GitHub profile / repos (prefer API over generic web search)
        if re.search(r"github\.com/[\w-]+", low) or re.search(
            r"\b(my\s+)?github\s+(projects?|repos?|profile)\b", low
        ):
            um = re.search(r"(https?://)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)", g, re.I)
            user = ""
            if um:
                user = um.group(0)
                if not user.startswith("http"):
                    user = "https://" + user
            if not user:
                user = "https://github.com/syedfahadiqbal7/"
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"List GitHub repos",
                    "tool": "github_list_repos",
                    "arguments": {"username_or_url": user, "max_repos": 30},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # In-app / voice search (results in transcript, no browser)
        m = re.search(
            r"(?:search\s+in[- ]?app(?:\s+for)?|in[- ]?app\s+search(?:\s+for)?|"
            r"voice\s+search(?:\s+for)?|search\s+by\s+voice(?:\s+for)?|"
            r"search\s+here\s+for|find\s+in\s+app)\s+(.+)$",
            g,
            re.I,
        )
        if m:
            q = m.group(1).strip().rstrip("?.")
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"In-app search: {q}",
                    "tool": "web_search_inapp",
                    "arguments": {"query": q, "max_results": 5},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # Browser: search web
        m = re.search(
            r"(?:search\s+(?:the\s+)?(?:web|google|bing)\s+for|google)\s+(.+)$",
            g,
            re.I,
        )
        if m:
            q = m.group(1).strip().rstrip(".")
            eng = "bing" if "bing" in low else "google"
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Web search: {q}",
                    "tool": "web_search_open",
                    "arguments": {"query": q, "engine": eng},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # Office open
        m = re.search(
            r"(?:open|launch|start)\s+(word|excel|powerpoint|outlook|onenote)\b",
            low,
        )
        if m:
            app = m.group(1)
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Open {app}",
                    "tool": "office_open",
                    "arguments": {"app": app},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # Office new csv/html on desktop
        m = re.search(
            r"(?:create|make|new)\s+(?:a\s+)?(?:excel\s+)?(?:csv|spreadsheet|html\s+doc(?:ument)?)\s+"
            r"(?:named|called)?\s*([a-zA-Z0-9_.\-]+)?",
            g,
            re.I,
        )
        if m and any(k in low for k in ("csv", "spreadsheet", "html")):
            kind = "html" if "html" in low else "csv"
            name = (m.group(1) or ("sheet.csv" if kind == "csv" else "doc.html")).strip()
            return {
                "goal": g,
                "needs_confirmation": True,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Create {kind} {name}",
                    "tool": "office_new",
                    "arguments": {"kind": kind, "name": name, "open_after": True},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # VS Code open path
        m = re.search(
            r"(?:open\s+(?:in\s+)?(?:vs\s*code|vscode|code)|code\s+open)\s+(.+)$",
            g,
            re.I,
        )
        if m or re.search(r"open\s+(.+)\s+in\s+(?:vs\s*code|vscode)\b", g, re.I):
            m2 = m or re.search(r"open\s+(.+)\s+in\s+(?:vs\s*code|vscode)\b", g, re.I)
            path = m2.group(1).strip().strip('"')
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Open in VS Code: {path}",
                    "tool": "vscode_open",
                    "arguments": {"path": path, "editor": "code"},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }


        # 4) Open app — pass friendly name; open_app resolves on the system
        # (URLs / Office / VS Code handled above in skill outcomes)
        m = re.search(
            r"(?:i\s+need|open|launch|start)\s+(.+)$",
            g,
            re.I,
        )
        if m and re.match(
            r"^(?:i\s+need|open|launch|start)\b",
            low,
        ):
            app = m.group(1).strip().rstrip(".")
            al = app.lower()
            # skip if skill packs should handle
            if (
                al.startswith("http://")
                or al.startswith("https://")
                or al.startswith("www.")
                or al in ("word", "excel", "powerpoint", "outlook", "onenote")
                or "vs code" in al
                or al.startswith("vscode")
                or al.startswith("in vscode")
                or al.startswith("in vs code")
                or " file " in al
                or "folder" in al
            ):
                app = ""
            if app and len(app) < 60:
                return {
                    "goal": g,
                    "needs_confirmation": False,
                    "source": "outcome_compiler",
                    "steps": [
                        {
                            "id": 1,
                            "title": f"Open {app}",
                            "tool": "open_app",
                            "arguments": {"target": app},
                            "risk": "low",
                            "success_check": {"type": "always_true"},
                        }
                    ],
                }





        # Create / write file (.txt via write_developer_code, .docx via write_docx)
        if re.search(r"\b(create|write|save|make)\s+(a\s+)?file\b", low) or (
            re.search(r"\b(create|write|save)\b", low)
            and _extract_document_path(g)
            and re.search(r"\.(txt|md|csv|json|log|docx|pdf)\b", low)
        ):
            path = _extract_document_path(g)
            if path:
                content = ""
                cm = re.search(
                    r"(?:with(?:\s+the)?\s+content(?:\s+of)?\s*:?\s*)([\s\S]+)$",
                    g,
                    re.I,
                )
                if cm:
                    content = cm.group(1).strip()
                    content = re.sub(
                        r"\s*(we just agreed|from (the )?conversation|as agreed)\.?\s*$",
                        "",
                        content,
                        flags=re.I,
                    ).strip()
                if re.search(
                    r"we just agreed|from (this|the) conversation|as agreed|content we",
                    low,
                ):
                    content = content or "__FROM_LAST_ASSISTANT__"
                if not content:
                    content = "(paste content)"

                lower_path = path.lower()
                # PDF: if user asked generate pdf from agreed content or from docx
                if lower_path.endswith(".pdf") or re.search(
                    r"\b(generate|create|export|make)\s+(a\s+)?pdf\b", low
                ):
                    if not lower_path.endswith(".pdf"):
                        # derive from any path in message or default
                        path = re.sub(r"\.(docx|txt)$", ".pdf", path, flags=re.I)
                        if not path.lower().endswith(".pdf"):
                            path = path + ".pdf"
                    # Prefer convert sibling docx if mentioned, else text content
                    docx_src = ""
                    if re.search(r"from\s+(the\s+)?(word|docx|document)", low):
                        docx_src = re.sub(r"\.pdf$", ".docx", path, flags=re.I)
                    args = {"filepath": path, "content": content}
                    if docx_src:
                        args = {"filepath": path, "source_docx": docx_src, "content": ""}
                    return {
                        "goal": g,
                        "needs_confirmation": True,
                        "source": "outcome_compiler",
                        "steps": [{
                            "id": 1,
                            "title": f"Create PDF {Path(path).name}",
                            "tool": "write_pdf",
                            "arguments": args,
                            "risk": "low",
                            "success_check": {"type": "file_exists", "path": path},
                        }],
                    }

                if lower_path.endswith(".docx") or re.search(
                    r"\b(word|docx)\b", low
                ):
                    if not lower_path.endswith(".docx"):
                        path = path + ("" if path.lower().endswith(".docx") else ".docx")
                    return {
                        "goal": g,
                        "needs_confirmation": True,
                        "source": "outcome_compiler",
                        "steps": [{
                            "id": 1,
                            "title": f"Create Word {Path(path).name}",
                            "tool": "write_docx",
                            "arguments": {
                                "filepath": path,
                                "content": content,
                            },
                            "risk": "low",
                            "success_check": {"type": "file_exists", "path": path},
                        }],
                    }

                # default text
                out_path = path
                return {
                    "goal": g,
                    "needs_confirmation": True,
                    "source": "outcome_compiler",
                    "steps": [{
                        "id": 1,
                        "title": f"Create file {Path(out_path).name}",
                        "tool": "write_developer_code",
                        "arguments": {"filepath": out_path, "content": content},
                        "risk": "low",
                        "success_check": {"type": "file_exists", "path": out_path},
                    }],
                }

        # Generate PDF explicitly (even without 'file' word)
        if re.search(r"\b(generate|create|export|make)\s+(a\s+)?pdf\b", low) or (
            re.search(r"\bpdf\b", low) and re.search(r"\b(generate|export|create)\b", low)
        ):
            path = _extract_document_path(g)
            if path and not path.lower().endswith(".pdf"):
                path = re.sub(r"\.(docx|txt)$", ".pdf", path, flags=re.I)
                if not path.lower().endswith(".pdf"):
                    path = str(Path(path).with_suffix(".pdf"))
            if not path:
                path = "Desktop/ForJob/CV_Versions/export.pdf"
            content = "__FROM_LAST_ASSISTANT__" if re.search(
                r"we just agreed|from (this|the) conversation|as agreed|master cv|ats",
                low,
            ) else ""
            # If references existing docx path
            docx_guess = ""
            mdocx = re.search(
                r"((?:[A-Za-z]:\\|Desktop|Documents)[^\s\"']+\.docx)",
                g,
                re.I,
            )
            if mdocx:
                docx_guess = mdocx.group(1)
            args = {"filepath": path}
            if docx_guess:
                args["source_docx"] = docx_guess
            elif content:
                args["content"] = content
            else:
                args["content"] = "__FROM_LAST_ASSISTANT__"
            return {
                "goal": g,
                "needs_confirmation": True,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": f"Create PDF {Path(path).name}",
                    "tool": "write_pdf",
                    "arguments": args,
                    "risk": "low",
                    "success_check": {"type": "file_exists", "path": path},
                }],
            }

        # General: read / audit / check any local document path (flexible wording)
        # Never steal "Create file ...docx" — that is a write intent
        path = _extract_document_path(g)
        create_intent = bool(re.search(
            r"\b(create|write|save|make)\b.*\b(file|document|cv|docx|txt|md)\b|"
            r"\b(create|write|save)\s+file\b",
            low,
        ))
        wants_doc = bool(re.search(
            r"\b(audit|review|analyse|analyze|read|check|extract|inspect|"
            r"summarise|summarize|document|cv|resume|"
            r"isme|is\s+me|kya\s+hai)\b",
            low,
        ))
        if path and wants_doc and not create_intent:
                return {
                    "goal": g,
                    "needs_confirmation": False,
                    "source": "outcome_compiler",
                    "steps": [
                        {
                            "id": 1,
                            "title": f"Read document {Path(path).name}",
                            "tool": "read_document",
                            "arguments": {"filepath": path, "max_chars": 15000},
                            "risk": "low",
                            "success_check": {"type": "always_true"},
                        }
                    ],
                }


        # ForJob / Mogi-style application agent commands
        if re.search(
            r"\b(mogi|job application agent|forjob agent|forjob application)\b",
            low,
        ) or re.search(r"\bforjob daily\b|\bdaily job plan\b", low):
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": "ForJob daily plan",
                    "tool": "forjob_daily_plan",
                    "arguments": {"day": "today"},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        if re.search(r"\b(score (this )?jd|match (this )?job|score (this )?job)\b", low) or (
            "job description" in low and ("score" in low or "match" in low)
        ):
            # Pass full user text as JD if long; else still call tool with description=g
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": "Score job description",
                    "tool": "forjob_score_jd",
                    "arguments": {
                        "job_description": g,
                        "job_title": "",
                        "company": "",
                    },
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        if re.search(r"\b(apply checklist|easy apply checklist|application checklist)\b", low):
            at = "easy_apply" if "easy" in low else ("external" if "external" in low else "unknown")
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": "Application checklist",
                    "tool": "forjob_apply_checklist",
                    "arguments": {"application_type": at},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # ForJob ATS keyword check
        if re.search(r"\b(ats|keyword coverage|ats keywords?)\b", low) and (
            "cv" in low or "resume" in low or "architect" in low or "banking" in low or "payments" in low
        ):
            # try extract path from message or default recent ForJob profile pattern
            pm = re.search(
                r"((?:[A-Za-z]:)?[^\s\"']+\.(?:docx|txt|pdf))",
                g,
            )
            path = pm.group(1) if pm else ""
            # fallback: if user only said keywords after audit, still require path — try ForJob Profile glob not available
            args = {"target_role": "both"}
            if path:
                args["filepath"] = path
            else:
                # common path hint from prior ForJob layout
                args["filepath"] = (
                    str(__import__("pathlib").Path.home() / "Desktop" / "ForJob" / "Profile" / "01_Profile")
                )
                # tool needs file not dir — leave empty and fail clearly if no path
                # Better: search message for Audit path stored — user must pass path
            # Prefer explicit file; if directory-only, tool fails — ask path
            if path:
                return {
                    "goal": g,
                    "needs_confirmation": False,
                    "source": "outcome_compiler",
                    "steps": [{
                        "id": 1,
                        "title": "ATS check CV",
                        "tool": "forjob_ats_check",
                        "arguments": {"filepath": path, "target_role": "both"},
                        "risk": "low",
                        "success_check": {"type": "always_true"},
                    }],
                }
            # No path: still try default CV filename under ForJob
            default_cv = str(
                __import__("pathlib").Path.home()
                / "Desktop" / "ForJob" / "Profile" / "01_Profile"
                / "Syed_Fahad_Iqbal_Updated_CV_2026.docx"
            )
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [{
                    "id": 1,
                    "title": "ATS check default ForJob CV",
                    "tool": "forjob_ats_check",
                    "arguments": {"filepath": default_cv, "target_role": "both"},
                    "risk": "low",
                    "success_check": {"type": "always_true"},
                }],
            }

        # ForJob master Excel tracker
        if (
            "forjob_master_job_tracker" in low
            or "forjob master job tracker" in low
            or (
                "excel" in low
                and "forjob" in low
                and ("tracker" in low or "workbook" in low or "job application" in low)
            )
            or (
                "create an excel workbook named forjob" in low
                or "forjob_master_job_tracker.xlsx" in low
            )
        ):
            return {
                "goal": g,
                "needs_confirmation": True,
                "source": "outcome_compiler",
                "steps": [
                    {
                        "id": 1,
                        "title": "Create ForJob Excel tracker",
                        "tool": "forjob_create_tracker",
                        "arguments": {},
                        "risk": "low",
                        "success_check": {"type": "always_true"},
                    }
                ],
            }

        # 4b) List installed applications (no LLM) — parse filter/limit from text
        if re.search(
            r"(?:list|show|what|which)\s+(?:all\s+)?(?:the\s+)?(?:installed\s+)?(?:apps?|applications?)\b|"
            r"(?:apps?|applications?)\s+installed\s+on\s+my|"
            r"which\s+application\s+is\s+installed|"
            r"list\s+installed\s+apps\b|"
            r"tell\s+me\s+which\s+application",
            low,
        ):
            args: Dict[str, Any] = {}
            # filter: "filter visual" | "filter: adobe" | "apps matching office"
            fm = re.search(
                r"filter\s*[:=]?\s*([a-zA-Z0-9_.+\-]+)|"
                r"(?:matching|named)\s+([a-zA-Z0-9_.+\-]+)",
                g,
                re.I,
            )
            if fm:
                args["filter"] = (fm.group(1) or fm.group(2) or "").strip()
            # limit: "limit 300" | "limit: 50" | "top 20"
            lm = re.search(r"(?:limit|top)\s*[:=]?\s*(\d+)", g, re.I)
            if lm:
                args["limit"] = int(lm.group(1))
            else:
                args["limit"] = 150
            return {
                "goal": g,
                "needs_confirmation": False,
                "source": "outcome_compiler",
                "steps": [
                    {
                        "id": 1,
                        "title": "List installed applications",
                        "tool": "list_installed_apps",
                        "arguments": args,
                        "risk": "low",
                        "success_check": {"type": "always_true"},
                    }
                ],
            }

        # 5) "Save this idea" → desktop ideas.txt
        m = re.search(
            r"(?:save|store)\s+(?:this\s+)?(?:idea|thought)\s*[:\-]?\s*(.+)$",
            g,
            re.I,
        )
        if m:
            idea = m.group(1).strip()
            path = _desktop("ideas.txt")
            content = idea + "\n"
            if os.path.isfile(path):
                try:
                    prev = Path(path).read_text(encoding="utf-8", errors="ignore")
                    content = prev.rstrip("\n") + "\n" + idea + "\n"
                except Exception:
                    pass
            return self._file_plan(g, path, content, verify_text=idea, title="Save idea")


        return None

    def _file_plan(
        self,
        goal: str,
        path: str,
        content: str,
        verify_text: Optional[str] = None,
        title: str = "Write file",
    ) -> Dict[str, Any]:
        # Verify against the meaningful snippet (not full append buffer)
        needle = (verify_text if verify_text is not None else content).strip()
        if len(needle) > 80:
            needle = needle[:80]
        if needle:
            success_check: Dict[str, Any] = {
                "type": "file_contains",
                "path": path,
                "text": needle,
            }
        else:
            success_check = {"type": "file_exists", "path": path}
        return {
            "goal": goal,
            "needs_confirmation": True,
            "source": "outcome_compiler",
            "steps": [
                {
                    "id": 1,
                    "title": title,
                    "tool": "write_developer_code",
                    "arguments": {"filepath": path, "content": content},
                    "risk": "low",
                    "success_check": success_check,
                }
            ],
        }
