# ui/skill_author.py
"""Skill Author — create/edit skills; right panel lists installed skills."""

from __future__ import annotations

import customtkinter as ctk
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from tkinter import filedialog, messagebox

from agent.skill_author import (
    parse_skill_markdown,
    plain_text_to_skill,
    skill_to_markdown,
    validate_skill,
    install_skill,
    load_all_custom_manifests,
)
from agent.skill_packs import PACK_CATALOG
from agent.plugin_marketplace import install_from_zip
from agent.app_restart import restart_app

_open_viewer = None

SAMPLE_TEMPLATE = """# skill_id: morning_brief
title: Morning Brief
description: Open calendar and create a morning notes file
triggers:
- morning brief
- start my day
actions:
- tool: browser_open
  url: https://calendar.google.com
- tool: office_new
  kind: txt
  name: morning_notes.txt
samples:
- morning brief
- start my day
"""


class SkillAuthorViewer(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        on_installed: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        skill_packs=None,
    ):
        global _open_viewer
        if _open_viewer is not None:
            try:
                if _open_viewer.winfo_exists():
                    _open_viewer.lift()
                    _open_viewer.focus_force()
                    self.withdraw()
                    self.after(10, self.destroy)
                    return
            except Exception:
                _open_viewer = None

        super().__init__(parent)
        _open_viewer = self
        self.title("Skill Author — create / update skills")
        self.geometry("980x680")
        self.on_installed = on_installed
        self.on_transcript = on_transcript
        self.skill_packs = skill_packs
        self._draft = None
        self._loaded_skill_id: Optional[str] = None
        self._loaded_original_md: str = ""

        self.transient(parent)
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        ctk.CTkLabel(
            self,
            text="Left: edit skill (plain text or markdown). Right: installed skills — click to load.",
            text_color="gray",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=(10, 4))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=4)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        # ----- LEFT: editor -----
        left = ctk.CTkFrame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ctk.CTkLabel(left, text="Skill editor", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 2)
        )
        self.input = ctk.CTkTextbox(left, height=220)
        self.input.pack(fill="both", expand=True, padx=10, pady=4)
        self.input.insert("1.0", SAMPLE_TEMPLATE)

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=4)
        ctk.CTkButton(row, text="Import .txt/.md", width=110, command=self._import).pack(
            side="left", padx=(0, 4)
        )
        ctk.CTkButton(
            row, text="Format / Preview", width=120, fg_color="#8E44AD", command=self._preview
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(row, text="New template", width=100, command=self._template).pack(
            side="left", padx=(0, 4)
        )

        row2 = ctk.CTkFrame(left, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=4)
        ctk.CTkButton(
            row2,
            text="Accept & Install",
            width=120,
            fg_color="#27AE60",
            hover_color="#1E8449",
            command=self._install_new,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            row2,
            text="Update skill",
            width=110,
            fg_color="#F39C12",
            hover_color="#D68910",
            command=self._update_skill,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            row2, text="Install plugin .zip", width=130, fg_color="#2980B9", command=self._install_plugin_zip
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            row2,
            text="Restart app",
            width=100,
            fg_color="#C0392B",
            hover_color="#922B21",
            command=self._restart,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkLabel(left, text="Review (markdown)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(6, 2)
        )
        self.preview = ctk.CTkTextbox(left, height=160)
        self.preview.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # ----- RIGHT: skill list -----
        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew")

        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(head, text="Available skills", font=ctk.CTkFont(weight="bold")).pack(
            side="left"
        )
        ctk.CTkButton(head, text="Refresh", width=70, command=self._reload_list).pack(side="right")

        self.list_frame = ctk.CTkScrollableFrame(right, width=280)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=4)

        self.hint = ctk.CTkLabel(
            right,
            text="Custom skills open in editor.\nBuiltin packs are enable/disable only.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
            justify="left",
        )
        self.hint.pack(anchor="w", padx=10, pady=(0, 8))

        self.status = ctk.CTkLabel(self, text="", text_color="#1ABC9C")
        self.status.pack(anchor="w", padx=16, pady=(2, 10))

        self._reload_list()

    def _on_close(self):
        global _open_viewer
        _open_viewer = None
        self.destroy()

    def _template(self):
        self._loaded_skill_id = None
        self._loaded_original_md = ""
        self.input.delete("1.0", "end")
        self.input.insert("1.0", SAMPLE_TEMPLATE)
        self.preview.delete("1.0", "end")
        self.status.configure(text="Template loaded (new skill).", text_color="#1ABC9C")

    def _import(self):
        path = filedialog.askopenfilename(
            title="Import skill",
            filetypes=[("Text/Markdown", "*.txt *.md"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            self._loaded_skill_id = None
            self._loaded_original_md = ""
            self.input.delete("1.0", "end")
            self.input.insert("1.0", text)
            self.status.configure(text=f"Loaded {path}", text_color="#1ABC9C")
            self._preview()
        except Exception as e:
            self.status.configure(text=f"Import failed: {e}", text_color="#E74C3C")

    def _looks_markdown(self, text: str) -> bool:
        t = text.lower()
        return "skill_id:" in t or (t.strip().startswith("#") and "triggers:" in t)

    def _parse_editor(self) -> Optional[Dict[str, Any]]:
        text = self.input.get("1.0", "end").strip()
        if not text:
            return None
        if self._looks_markdown(text):
            return parse_skill_markdown(text)
        return plain_text_to_skill(text)

    def _preview(self):
        try:
            skill = self._parse_editor()
            if not skill:
                self.status.configure(text="Empty input", text_color="#E74C3C")
                return
            self._draft = skill
            md = skill_to_markdown(skill)
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", md)
            ok, errors = validate_skill(skill)
            if ok:
                self.status.configure(
                    text=f"Preview OK: {skill.get('skill_id')}",
                    text_color="#27AE60",
                )
            else:
                self.status.configure(text="Fix: " + "; ".join(errors), text_color="#E74C3C")
        except Exception as e:
            self.status.configure(text=f"Preview error: {e}", text_color="#E74C3C")

    def _skill_from_preview_or_editor(self) -> Optional[Dict[str, Any]]:
        text = self.preview.get("1.0", "end").strip()
        if text:
            return parse_skill_markdown(text)
        return self._parse_editor()

    def _install_new(self):
        try:
            skill = self._skill_from_preview_or_editor()
            if not skill:
                self._preview()
                skill = self._skill_from_preview_or_editor()
            if not skill:
                self.status.configure(text="Nothing to install", text_color="#E74C3C")
                return
            ok, errors = validate_skill(skill)
            if not ok:
                self.status.configure(text="Blocked: " + "; ".join(errors), text_color="#E74C3C")
                return
            # If overwriting existing, ask
            existing_ids = {m.get("skill_id") for m in load_all_custom_manifests()}
            if skill.get("skill_id") in existing_ids:
                if not messagebox.askyesno(
                    "Overwrite skill?",
                    f"Skill '{skill.get('skill_id')}' already exists.\n\n"
                    "Install will overwrite it. Continue?\n"
                    "(Prefer Update skill when editing.)",
                    parent=self,
                ):
                    return
            result = install_skill(skill)
            if not result.get("ok"):
                self.status.configure(
                    text="Install failed: " + "; ".join(result.get("errors") or []),
                    text_color="#E74C3C",
                )
                return
            self._after_save(result)
        except Exception as e:
            self.status.configure(text=f"Install error: {e}", text_color="#E74C3C")

    def _update_skill(self):
        """Update currently loaded / edited skill with confirmation of changes."""
        try:
            skill = self._skill_from_preview_or_editor()
            if not skill:
                self._preview()
                skill = self._skill_from_preview_or_editor()
            if not skill:
                self.status.configure(text="Nothing to update — load a skill first", text_color="#E74C3C")
                return
            ok, errors = validate_skill(skill)
            if not ok:
                self.status.configure(text="Blocked: " + "; ".join(errors), text_color="#E74C3C")
                return

            new_md = skill_to_markdown(skill)
            sid = skill.get("skill_id")
            # Prefer loaded id if user didn't change skill_id
            if self._loaded_skill_id and sid != self._loaded_skill_id:
                if not messagebox.askyesno(
                    "Skill ID changed",
                    f"Original skill_id: {self._loaded_skill_id}\n"
                    f"New skill_id: {sid}\n\n"
                    "This will save as a different skill (or rename target).\nContinue?",
                    parent=self,
                ):
                    return

            # Show change confirmation
            old = self._loaded_original_md.strip() or "(new / not loaded from list)"
            # Compact diff-ish message
            msg = (
                f"Update skill '{sid}'?\n\n"
                f"Title: {skill.get('title')}\n"
                f"Triggers: {', '.join(skill.get('triggers') or [])}\n"
                f"Actions: {len(skill.get('actions') or [])}\n\n"
                "This overwrites the installed skill files.\n"
                "Restart app after update to reload plugins."
            )
            if old and old != new_md.strip():
                msg = "Changes detected from the version you loaded.\n\n" + msg
            if not messagebox.askyesno("Confirm skill update", msg, parent=self):
                self.status.configure(text="Update cancelled.", text_color="#F39C12")
                return

            result = install_skill(skill)
            if not result.get("ok"):
                self.status.configure(
                    text="Update failed: " + "; ".join(result.get("errors") or []),
                    text_color="#E74C3C",
                )
                return
            self._loaded_skill_id = sid
            self._loaded_original_md = new_md
            self._after_save(result, updated=True)
        except Exception as e:
            self.status.configure(text=f"Update error: {e}", text_color="#E74C3C")

    def _after_save(self, result: Dict[str, Any], updated: bool = False):
        if self.skill_packs:
            try:
                self.skill_packs.reload_custom()
            except Exception:
                pass
        how = result.get("how_to") or "Saved."
        verb = "Updated" if updated else "Installed"
        self.status.configure(
            text=f"{verb} {result.get('skill_id')}. Click Restart app to load.",
            text_color="#27AE60",
        )
        if self.on_transcript:
            self.on_transcript(f"[Skill Author]\n{how}")
        if self.on_installed:
            self.on_installed(how)
        self._reload_list()

    def _install_plugin_zip(self):
        path = filedialog.askopenfilename(
            title="Install plugin package",
            filetypes=[("Plugin zip", "*.zip"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            res = install_from_zip(path)
            if not res.get("ok"):
                self.status.configure(
                    text="Plugin install failed: " + "; ".join(res.get("errors") or []),
                    text_color="#E74C3C",
                )
                return
            msg = res.get("message") or "Plugin installed."
            self.status.configure(text=msg + " Click Restart app.", text_color="#27AE60")
            if self.on_transcript:
                self.on_transcript("[Marketplace] " + msg)
            if self.on_installed:
                self.on_installed(msg)
        except Exception as e:
            self.status.configure(text=f"Plugin install error: {e}", text_color="#E74C3C")

    def _restart(self):
        try:
            if self.on_transcript:
                self.on_transcript("[System] Restarting application to load plugins/skills...")
            r = self
            while True:
                try:
                    m = r.master
                    if m is None:
                        break
                    r = m
                except Exception:
                    break
            restart_app(r)
        except Exception as e:
            self.status.configure(text=f"Restart failed: {e}", text_color="#E74C3C")

    def _reload_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        # Builtin packs
        ctk.CTkLabel(
            self.list_frame, text="Builtin packs", text_color="gray", font=ctk.CTkFont(size=11)
        ).pack(anchor="w", pady=(4, 2))
        enabled_map = {}
        if self.skill_packs:
            try:
                enabled_map = dict(self.skill_packs.enabled)
            except Exception:
                enabled_map = {}
        for pid, meta in PACK_CATALOG.items():
            if meta.get("custom"):
                continue
            on = enabled_map.get(pid, meta.get("default_enabled", True))
            row = ctk.CTkFrame(self.list_frame, fg_color=("gray90", "gray20"))
            row.pack(fill="x", pady=2)
            label = f"{'[ON] ' if on else '[OFF] '}{meta.get('title') or pid}"
            ctk.CTkLabel(row, text=label, anchor="w").pack(side="left", padx=6, pady=4)
            ctk.CTkButton(
                row,
                text="Toggle",
                width=60,
                command=lambda p=pid, state=on: self._toggle_pack(p, not state),
            ).pack(side="right", padx=4, pady=2)

        # Custom skills
        ctk.CTkLabel(
            self.list_frame, text="Custom skills", text_color="gray", font=ctk.CTkFont(size=11)
        ).pack(anchor="w", pady=(10, 2))
        customs = load_all_custom_manifests()
        if not customs:
            ctk.CTkLabel(
                self.list_frame, text="(none yet — Accept & Install)", text_color="gray"
            ).pack(anchor="w", padx=4)
            return
        for man in sorted(customs, key=lambda m: str(m.get("title") or m.get("skill_id"))):
            sid = man.get("skill_id")
            title = man.get("title") or sid
            row = ctk.CTkFrame(self.list_frame, fg_color=("gray90", "gray20"))
            row.pack(fill="x", pady=2)
            btn = ctk.CTkButton(
                row,
                text=f"✎ {title}",
                anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray30"),
                command=lambda m=man: self._load_custom(m),
            )
            btn.pack(fill="x", padx=2, pady=2)

    def _toggle_pack(self, pack_id: str, on: bool):
        if not self.skill_packs:
            self.status.configure(text="Skill packs manager not available", text_color="#E74C3C")
            return
        try:
            self.skill_packs.set_enabled(pack_id, on)
            self.status.configure(
                text=f"Pack '{pack_id}' {'enabled' if on else 'disabled'}.",
                text_color="#27AE60",
            )
            if self.on_transcript:
                self.on_transcript(f"[Skill] {'Enabled' if on else 'Disabled'} pack: {pack_id}")
            self._reload_list()
        except Exception as e:
            self.status.configure(text=str(e), text_color="#E74C3C")

    def _load_custom(self, man: Dict[str, Any]):
        """Load custom skill manifest into editor as markdown."""
        try:
            # Reconstruct skill dict for markdown
            skill = {
                "skill_id": man.get("skill_id"),
                "title": man.get("title"),
                "description": man.get("description"),
                "triggers": man.get("triggers") or [],
                "actions": man.get("actions") or [],
                "samples": man.get("samples") or man.get("triggers") or [],
            }
            md = skill_to_markdown(skill)
            self.input.delete("1.0", "end")
            self.input.insert("1.0", md)
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", md)
            self._draft = skill
            self._loaded_skill_id = skill.get("skill_id")
            self._loaded_original_md = md
            self.status.configure(
                text=f"Loaded '{self._loaded_skill_id}' — edit then Update skill.",
                text_color="#1ABC9C",
            )
        except Exception as e:
            self.status.configure(text=f"Load failed: {e}", text_color="#E74C3C")


def open_skill_author(parent, on_installed=None, on_transcript=None, skill_packs=None):
    global _open_viewer
    if _open_viewer is not None:
        try:
            if _open_viewer.winfo_exists():
                _open_viewer.lift()
                return _open_viewer
        except Exception:
            _open_viewer = None
    return SkillAuthorViewer(
        parent,
        on_installed=on_installed,
        on_transcript=on_transcript,
        skill_packs=skill_packs,
    )
