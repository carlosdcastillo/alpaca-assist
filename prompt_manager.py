"""Prompt Manager - Manages custom prompts with triggers and bodies."""
import json
import os
import tkinter as tk
from tkinter import messagebox
from tkinter import scrolledtext
from tkinter import ttk
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


class Prompt:
    """Represents a single prompt with trigger and body."""

    def __init__(self, trigger: str, body: str, description: str = ""):
        self.trigger = trigger
        self.body = body
        self.description = description

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger,
            "body": self.body,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Prompt":
        return cls(
            trigger=data.get("trigger", ""),
            body=data.get("body", ""),
            description=data.get("description", ""),
        )


class PromptManager:
    """Manages a collection of prompts with persistence."""

    def __init__(self, prompts_file: str = "prompts.json"):
        self.prompts_file = prompts_file
        self.prompts: list[Prompt] = []
        self.load_prompts()

    def load_prompts(self) -> None:
        """Load prompts from JSON file."""
        if os.path.exists(self.prompts_file):
            try:
                with open(self.prompts_file, encoding="utf-8") as f:
                    data = json.load(f)
                    self.prompts = [
                        Prompt.from_dict(p) for p in data.get("prompts", [])
                    ]
            except (json.JSONDecodeError, OSError) as e:
                print(f"Error loading prompts: {e}")
                self.prompts = []
                self._initialize_default_prompts()
        else:
            self._initialize_default_prompts()
            self.save_prompts()

    def _initialize_default_prompts(self) -> None:
        """Initialize with some default prompts."""
        self.prompts = [
            Prompt(
                "explain",
                "Please explain this concept in simple terms:",
                "Request a simple explanation",
            ),
            Prompt(
                "summarize",
                "Please provide a concise summary of the following:",
                "Request a summary",
            ),
            Prompt(
                "code_review",
                "Please review this code and provide feedback on:\n- Code quality\n- Potential bugs\n- Performance improvements\n- Best practices",
                "Request a code review",
            ),
            Prompt(
                "debug",
                "Please help me debug this issue. The problem is:",
                "Request debugging help",
            ),
            Prompt(
                "refactor",
                "Please refactor this code to improve:\n- Readability\n- Maintainability\n- Performance",
                "Request code refactoring",
            ),
        ]

    def save_prompts(self) -> None:
        """Save prompts to JSON file."""
        try:
            data = {"prompts": [p.to_dict() for p in self.prompts]}
            with open(self.prompts_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"Error saving prompts: {e}")

    def add_prompt(self, prompt: Prompt) -> None:
        """Add a new prompt."""
        self.prompts.append(prompt)
        self.save_prompts()

    def remove_prompt(self, index: int) -> None:
        """Remove a prompt by index."""
        if 0 <= index < len(self.prompts):
            del self.prompts[index]
            self.save_prompts()

    def update_prompt(self, index: int, prompt: Prompt) -> None:
        """Update an existing prompt."""
        if 0 <= index < len(self.prompts):
            self.prompts[index] = prompt
            self.save_prompts()

    def get_prompt_by_trigger(self, trigger: str) -> Prompt | None:
        """Get a prompt by its trigger."""
        for prompt in self.prompts:
            if prompt.trigger.lower() == trigger.lower():
                return prompt
        return None

    def get_triggers(self) -> list[str]:
        """Get list of all triggers."""
        return [p.trigger for p in self.prompts]

    def get_prompts_for_autocomplete(self) -> list[tuple[str, str]]:
        """Get prompts formatted for autocomplete (trigger, description)."""
        return [(p.trigger, p.description or p.trigger) for p in self.prompts]


class PromptManagerWindow:
    """Window for managing prompts."""

    def __init__(self, parent: tk.Tk, prompt_manager: PromptManager, preferences: dict):
        self.parent = parent
        self.prompt_manager = prompt_manager
        self.preferences = preferences
        self.window = None
        self.selected_index: int | None = None

    def _create_widgets(self) -> None:
        """Create the window widgets."""
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        ttk.Label(left_frame, text="Prompts:", font=("", 10, "bold")).pack(anchor=tk.W)
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.prompt_listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            font=(self.preferences.get("font_family", "Courier"), 10),
            selectmode=tk.SINGLE,
        )
        self.prompt_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.prompt_listbox.yview)
        self.prompt_listbox.bind("<<ListboxSelect>>", self._on_prompt_select)
        list_button_frame = ttk.Frame(left_frame)
        list_button_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(list_button_frame, text="Add", command=self._add_prompt).pack(
            side=tk.LEFT,
            padx=(0, 2),
        )
        ttk.Button(list_button_frame, text="Remove", command=self._remove_prompt).pack(
            side=tk.LEFT,
            padx=2,
        )
        ttk.Button(
            list_button_frame,
            text="Duplicate",
            command=self._duplicate_prompt,
        ).pack(side=tk.LEFT, padx=2)
        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        ttk.Label(right_frame, text="Edit Prompt:", font=("", 10, "bold")).pack(
            anchor=tk.W,
        )
        trigger_frame = ttk.Frame(right_frame)
        trigger_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(trigger_frame, text="Trigger:", width=12).pack(side=tk.LEFT)
        self.trigger_entry = ttk.Entry(
            trigger_frame,
            font=(self.preferences.get("font_family", "Courier"), 10),
        )
        self.trigger_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        desc_frame = ttk.Frame(right_frame)
        desc_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(desc_frame, text="Description:", width=12).pack(side=tk.LEFT)
        self.description_entry = ttk.Entry(
            desc_frame,
            font=(self.preferences.get("font_family", "Courier"), 10),
        )
        self.description_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(right_frame, text="Body:").pack(anchor=tk.W, pady=(5, 0))
        body_frame = ttk.Frame(right_frame)
        body_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        body_scrollbar = ttk.Scrollbar(body_frame)
        body_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.body_text = scrolledtext.ScrolledText(
            body_frame,
            wrap=tk.WORD,
            font=(self.preferences.get("font_family", "Courier"), 10),
            height=15,
            yscrollcommand=body_scrollbar.set,
        )
        self.body_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body_scrollbar.config(command=self.body_text.yview)
        editor_button_frame = ttk.Frame(right_frame)
        editor_button_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(
            editor_button_frame,
            text="Save Changes",
            command=self._save_changes,
        ).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(
            editor_button_frame,
            text="Revert",
            command=self._revert_changes,
        ).pack(side=tk.LEFT, padx=2)
        bottom_frame = ttk.Frame(self.window)
        bottom_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        ttk.Button(bottom_frame, text="Close", command=self.window.destroy).pack(
            side=tk.RIGHT,
        )
        ttk.Button(bottom_frame, text="Import", command=self._import_prompts).pack(
            side=tk.RIGHT,
            padx=(0, 5),
        )
        ttk.Button(bottom_frame, text="Export", command=self._export_prompts).pack(
            side=tk.RIGHT,
            padx=(0, 5),
        )

    def _refresh_prompt_list(self) -> None:
        """Refresh the prompt list display."""
        self.prompt_listbox.delete(0, tk.END)
        for prompt in self.prompt_manager.prompts:
            display_text = f"{prompt.trigger}"
            if prompt.description:
                display_text += f" - {prompt.description}"
            self.prompt_listbox.insert(tk.END, display_text)

    def _on_prompt_select(self, event: tk.Event) -> None:
        """Handle prompt selection."""
        selection = self.prompt_listbox.curselection()
        if selection:
            self.selected_index = selection[0]
            self._load_prompt_to_editor(self.selected_index)

    def _load_prompt_to_editor(self, index: int) -> None:
        """Load a prompt into the editor."""
        if 0 <= index < len(self.prompt_manager.prompts):
            prompt = self.prompt_manager.prompts[index]
            self.trigger_entry.delete(0, tk.END)
            self.trigger_entry.insert(0, prompt.trigger)
            self.description_entry.delete(0, tk.END)
            self.description_entry.insert(0, prompt.description)
            self.body_text.delete("1.0", tk.END)
            self.body_text.insert("1.0", prompt.body)

    def _save_changes(self) -> None:
        """Save changes to the selected prompt."""
        if self.selected_index is None:
            messagebox.showwarning(
                "No Selection",
                "Please select a prompt to save changes.",
            )
            return
        trigger = self.trigger_entry.get().strip()
        if not trigger:
            messagebox.showerror("Invalid Trigger", "Trigger cannot be empty.")
            return
        for i, prompt in enumerate(self.prompt_manager.prompts):
            if i != self.selected_index and prompt.trigger.lower() == trigger.lower():
                messagebox.showerror(
                    "Duplicate Trigger",
                    f"A prompt with trigger '{trigger}' already exists.",
                )
                return
        body = self.body_text.get("1.0", tk.END).strip()
        description = self.description_entry.get().strip()
        new_prompt = Prompt(trigger, body, description)
        self.prompt_manager.update_prompt(self.selected_index, new_prompt)
        self._refresh_prompt_list()
        self.prompt_listbox.selection_set(self.selected_index)
        messagebox.showinfo("Success", "Prompt saved successfully.")

    def _revert_changes(self) -> None:
        """Revert changes in the editor."""
        if self.selected_index is not None:
            self._load_prompt_to_editor(self.selected_index)

    def _add_prompt(self) -> None:
        """Add a new prompt."""
        new_prompt = Prompt("new_trigger", "Prompt body here...", "New prompt")
        self.prompt_manager.add_prompt(new_prompt)
        self._refresh_prompt_list()
        self.prompt_listbox.selection_set(len(self.prompt_manager.prompts) - 1)
        self.selected_index = len(self.prompt_manager.prompts) - 1
        self._load_prompt_to_editor(self.selected_index)
        self.trigger_entry.focus_set()
        self.trigger_entry.select_range(0, tk.END)

    def _remove_prompt(self) -> None:
        """Remove the selected prompt."""
        if self.selected_index is None:
            messagebox.showwarning("No Selection", "Please select a prompt to remove.")
            return
        if messagebox.askyesno(
            "Confirm Removal",
            "Are you sure you want to remove this prompt?",
        ):
            self.prompt_manager.remove_prompt(self.selected_index)
            self._refresh_prompt_list()
            self.trigger_entry.delete(0, tk.END)
            self.description_entry.delete(0, tk.END)
            self.body_text.delete("1.0", tk.END)
            self.selected_index = None

    def _duplicate_prompt(self) -> None:
        """Duplicate the selected prompt."""
        if self.selected_index is None:
            messagebox.showwarning(
                "No Selection",
                "Please select a prompt to duplicate.",
            )
            return
        original = self.prompt_manager.prompts[self.selected_index]
        base_trigger = original.trigger
        counter = 1
        new_trigger = f"{base_trigger}_copy"
        while any(
            p.trigger.lower() == new_trigger.lower()
            for p in self.prompt_manager.prompts
        ):
            counter += 1
            new_trigger = f"{base_trigger}_copy{counter}"
        new_prompt = Prompt(
            new_trigger,
            original.body,
            f"{original.description} (copy)" if original.description else "Copy",
        )
        self.prompt_manager.add_prompt(new_prompt)
        self._refresh_prompt_list()
        self.prompt_listbox.selection_set(len(self.prompt_manager.prompts) - 1)
        self.selected_index = len(self.prompt_manager.prompts) - 1
        self._load_prompt_to_editor(self.selected_index)

    def _import_prompts(self) -> None:
        """Import prompts from a file."""
        from tkinter import filedialog

        file_path = filedialog.askopenfilename(
            parent=self.window,
            title="Import Prompts",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if file_path:
            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
                    imported_prompts = [
                        Prompt.from_dict(p) for p in data.get("prompts", [])
                    ]
                if not imported_prompts:
                    messagebox.showwarning(
                        "No Prompts",
                        "No prompts found in the selected file.",
                    )
                    return
                existing_triggers = {
                    p.trigger.lower() for p in self.prompt_manager.prompts
                }
                conflicts = [
                    p
                    for p in imported_prompts
                    if p.trigger.lower() in existing_triggers
                ]
                if conflicts:
                    msg = f"Found {len(conflicts)} prompt(s) with existing triggers. Replace existing prompts?"
                    if not messagebox.askyesno("Trigger Conflicts", msg):
                        return
                    self.prompt_manager.prompts = [
                        p
                        for p in self.prompt_manager.prompts
                        if p.trigger.lower()
                        not in {c.trigger.lower() for c in conflicts}
                    ]
                for prompt in imported_prompts:
                    self.prompt_manager.add_prompt(prompt)
                self._refresh_prompt_list()
                messagebox.showinfo(
                    "Success",
                    f"Imported {len(imported_prompts)} prompt(s).",
                )
            except Exception as e:
                messagebox.showerror(
                    "Import Error",
                    f"Failed to import prompts: {str(e)}",
                )

    def _export_prompts(self) -> None:
        """Export prompts to a file."""
        from tkinter import filedialog

        file_path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export Prompts",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if file_path:
            try:
                data = {"prompts": [p.to_dict() for p in self.prompt_manager.prompts]}
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                messagebox.showinfo(
                    "Success",
                    f"Exported {len(self.prompt_manager.prompts)} prompt(s).",
                )
            except Exception as e:
                messagebox.showerror(
                    "Export Error",
                    f"Failed to export prompts: {str(e)}",
                )

    def _on_window_close(self) -> None:
        """Handle window close event with proper cleanup."""
        try:
            if self.window:
                self.window.grab_release()
                self.window.destroy()
        except tk.TclError:
            pass
        finally:
            self.window = None

    def show(self) -> None:
        """Show the prompt manager window."""
        if self.window and self.window.winfo_exists():
            try:
                self.window.lift()
                self.window.focus_force()
                return
            except tk.TclError:
                try:
                    self.window.destroy()
                except tk.TclError:
                    pass
                self.window = None
        self.window = tk.Toplevel(self.parent)
        self.window.title("Prompt Manager")
        self.window.geometry("800x600")
        try:
            self.window.transient(self.parent)
        except tk.TclError as e:
            print(f"Warning: Could not set window as transient: {e}")
        self._create_widgets()
        self._refresh_prompt_list()
        self.window.update_idletasks()
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        window_width = self.window.winfo_reqwidth()
        window_height = self.window.winfo_reqheight()
        x = max(0, (screen_width - window_width) // 2)
        y = max(0, (screen_height - window_height) // 2)
        self.window.geometry(f"800x600+{x}+{y}")
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)
        try:
            self.window.grab_set()
        except tk.TclError as e:
            print(f"Warning: Could not grab window focus: {e}")
