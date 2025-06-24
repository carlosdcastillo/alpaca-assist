import tkinter as tk
from tkinter import ttk
from typing import Any
from typing import List

# Default preferences
DEFAULT_PREFERENCES = {
    "api_url": "http://localhost:11434/api/chat",
    "default_model": "granite-code:8b",
    "summary_model": "codellama:13b",
    "font_family": "Cascadia Mono",
    "font_size": 12,
    "theme": "default",
    "background_color": "black",
    "window_geometry": "600x800+100+100",
    "auto_save": True,
    "ui_update_interval": 500,
    "max_undo_levels": -1,
    "chat_update_throttle": 0.1,
}


class PreferencesWindow:
    def __init__(self, parent: "ChatApp") -> None:
        self.parent = parent
        self.window = tk.Toplevel(parent.master)
        self.window.title("Preferences")
        self.window.geometry("500x400")
        self.window.resizable(True, True)

        # Make window modal
        self.window.transient(parent.master)
        self.window.grab_set()

        # Center the window
        self.center_window()

        # Store original preferences for cancel functionality
        self.original_prefs = parent.preferences.copy()

        self.create_widgets()

        # Handle window closing
        self.window.protocol("WM_DELETE_WINDOW", self.on_cancel)

    def center_window(self) -> None:
        """Center the preferences window on the parent window."""
        self.window.update_idletasks()

        # Get parent window position and size
        parent_x = self.parent.master.winfo_x()
        parent_y = self.parent.master.winfo_y()
        parent_width = self.parent.master.winfo_width()
        parent_height = self.parent.master.winfo_height()

        # Get preferences window size
        pref_width = 500
        pref_height = 400

        # Calculate center position
        x = parent_x + (parent_width - pref_width) // 2
        y = parent_y + (parent_height - pref_height) // 2

        self.window.geometry(f"{pref_width}x{pref_height}+{x}+{y}")

    def create_widgets(self) -> None:
        # Create notebook for different preference categories
        notebook = ttk.Notebook(self.window)
        notebook.pack(expand=True, fill="both", padx=10, pady=10)

        # API Settings Tab
        self.create_api_tab(notebook)

        # Appearance Tab
        self.create_appearance_tab(notebook)

        # Behavior Tab
        self.create_behavior_tab(notebook)

        # Create buttons frame
        button_frame = ttk.Frame(self.window)
        button_frame.pack(fill="x", padx=10, pady=(0, 10))

        # Buttons
        ttk.Button(
            button_frame,
            text="OK",
            command=self.on_ok,
        ).pack(side="right", padx=(5, 0))

        ttk.Button(
            button_frame,
            text="Cancel",
            command=self.on_cancel,
        ).pack(side="right")

        ttk.Button(
            button_frame,
            text="Apply",
            command=self.on_apply,
        ).pack(side="right", padx=(0, 5))

        ttk.Button(
            button_frame,
            text="Reset to Defaults",
            command=self.on_reset_defaults,
        ).pack(side="left")

    def create_api_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="API Settings")

        # API URL
        ttk.Label(frame, text="API URL:").grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.api_url_var = tk.StringVar(value=str(self.parent.preferences["api_url"]))
        api_url_entry = ttk.Entry(frame, textvariable=self.api_url_var, width=50)
        api_url_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        # Default Model
        ttk.Label(frame, text="Default Model:").grid(
            row=1,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.default_model_var = tk.StringVar(
            value=str(self.parent.preferences["default_model"]),
        )
        default_model_entry = ttk.Entry(
            frame,
            textvariable=self.default_model_var,
            width=50,
        )
        default_model_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # Summary Model
        ttk.Label(frame, text="Summary Model:").grid(
            row=2,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.summary_model_var = tk.StringVar(
            value=str(self.parent.preferences["summary_model"]),
        )
        summary_model_entry = ttk.Entry(
            frame,
            textvariable=self.summary_model_var,
            width=50,
        )
        summary_model_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

        # Test Connection Button
        test_button = ttk.Button(
            frame,
            text="Test Connection",
            command=self.test_connection,
        )
        test_button.grid(row=3, column=1, padx=5, pady=10, sticky="w")

        frame.columnconfigure(1, weight=1)

    def get_available_fonts(self) -> List[str]:
        """Get list of available monospace fonts on the system."""
        import tkinter.font as tkfont

        # Get all available font families
        all_fonts = list(tkfont.families())

        # Common monospace fonts to prioritize
        preferred_monospace = [
            "Cascadia Mono",
            "Cascadia Code",
            "Consolas",
            "Monaco",
            "Menlo",
            "DejaVu Sans Mono",
            "Liberation Mono",
            "Courier New",
            "Courier",
            "Ubuntu Mono",
            "Roboto Mono",
            "Source Code Pro",
            "Fira Code",
            "JetBrains Mono",
            "SF Mono",
        ]

        # Filter available fonts, prioritizing monospace ones
        available_fonts = []

        # Add preferred monospace fonts that are available
        for font in preferred_monospace:
            if font in all_fonts:
                available_fonts.append(font)

        # Add other available fonts (limit to reasonable number)
        other_fonts = [f for f in sorted(all_fonts) if f not in available_fonts]
        available_fonts.extend(other_fonts[:20])  # Limit to prevent overwhelming UI

        return available_fonts

    def validate_font(self, font_family: str, font_size: int) -> bool:
        """Validate if a font family and size combination works."""
        try:
            import tkinter.font as tkfont

            test_font = tkfont.Font(family=font_family, size=font_size)
            # Test if the font actually works by getting its metrics
            test_font.metrics()
            return True
        except Exception:
            return False

    def preview_font(self) -> None:
        """Show a preview of the selected font."""
        font_family = self.font_family_var.get()
        font_size = self.font_size_var.get()

        if not self.validate_font(font_family, font_size):
            messagebox.showwarning(
                "Font Preview",
                f"Font '{font_family}' at size {font_size} is not available or invalid.",
            )
            return

        # Create preview window
        preview_window = tk.Toplevel(self.window)
        preview_window.title(f"Font Preview: {font_family}")
        preview_window.geometry("400x200")

        sample_text = (
            f"Font: {font_family}, Size: {font_size}\n\n"
            "The quick brown fox jumps over the lazy dog.\n"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ\n"
            "abcdefghijklmnopqrstuvwxyz\n"
            "0123456789 !@#$%^&*()_+-=[]{}|;:,.<>?"
        )

        text_widget = tk.Text(
            preview_window,
            font=(font_family, font_size),
            wrap=tk.WORD,
        )
        text_widget.pack(expand=True, fill="both", padx=10, pady=10)
        text_widget.insert("1.0", sample_text)
        text_widget.config(state=tk.DISABLED)

    def create_appearance_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="Appearance")

        # Font Family - Use dynamic font list
        ttk.Label(frame, text="Font Family:").grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.font_family_var = tk.StringVar(
            value=str(self.parent.preferences["font_family"]),
        )

        # Get available fonts dynamically
        available_fonts = self.get_available_fonts()

        font_family_combo = ttk.Combobox(
            frame,
            textvariable=self.font_family_var,
            values=available_fonts,
            state="readonly",  # Prevent typing invalid font names
        )
        font_family_combo.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        # Add font preview button
        preview_button = ttk.Button(
            frame,
            text="Preview",
            command=self.preview_font,
        )
        preview_button.grid(row=0, column=2, padx=5, pady=5)

        # Font Size - this one is IntVar, so convert to int
        ttk.Label(frame, text="Font Size:").grid(
            row=1,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.font_size_var = tk.IntVar(
            value=int(str(self.parent.preferences["font_size"])),
        )
        font_size_spin = ttk.Spinbox(
            frame,
            from_=8,
            to=24,
            textvariable=self.font_size_var,
            width=10,
        )
        font_size_spin.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        # Background Color
        ttk.Label(frame, text="Background Color:").grid(
            row=2,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.background_color_var = tk.StringVar(
            value=str(self.parent.preferences["background_color"]),
        )
        background_colors = ["black", "white"]
        background_combo = ttk.Combobox(
            frame,
            textvariable=self.background_color_var,
            values=background_colors,
        )
        background_combo.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

        # Theme
        ttk.Label(frame, text="Syntax Theme:").grid(
            row=3,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.theme_var = tk.StringVar(value=str(self.parent.preferences["theme"]))

        # Get available themes
        from pygments.styles import get_all_styles

        available_themes = sorted(list(get_all_styles()))

        theme_combo = ttk.Combobox(
            frame,
            textvariable=self.theme_var,
            values=available_themes,
        )
        theme_combo.grid(row=3, column=1, padx=5, pady=5, sticky="ew")

        # Window Geometry
        ttk.Label(frame, text="Window Geometry:").grid(
            row=4,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.window_geometry_var = tk.StringVar(
            value=str(self.parent.preferences["window_geometry"]),
        )
        geometry_entry = ttk.Entry(
            frame,
            textvariable=self.window_geometry_var,
            width=50,
        )
        geometry_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")

        frame.columnconfigure(1, weight=1)

    def create_behavior_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="Behavior")

        # Auto Save
        self.auto_save_var = tk.BooleanVar(
            value=bool(self.parent.preferences["auto_save"]),
        )
        auto_save_check = ttk.Checkbutton(
            frame,
            text="Auto-save sessions",
            variable=self.auto_save_var,
        )
        auto_save_check.grid(row=0, column=0, sticky="w", padx=5, pady=5, columnspan=2)

        # UI Update Interval
        ttk.Label(frame, text="UI Update Interval (ms):").grid(
            row=1,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.ui_update_var = tk.IntVar(
            value=int(str(self.parent.preferences["ui_update_interval"])),
        )
        update_spin = ttk.Spinbox(
            frame,
            from_=100,
            to=2000,
            increment=100,
            textvariable=self.ui_update_var,
            width=10,
        )
        update_spin.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        # Max Undo Levels
        ttk.Label(frame, text="Max Undo Levels:").grid(
            row=2,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.max_undo_var = tk.IntVar(
            value=int(str(self.parent.preferences["max_undo_levels"])),
        )
        undo_spin = ttk.Spinbox(
            frame,
            from_=-1,
            to=100,
            textvariable=self.max_undo_var,
            width=10,
        )
        undo_spin.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        # Chat Update Throttle - Fixed row number
        ttk.Label(frame, text="Chat Update Throttle (ms):").grid(
            row=3,
            column=0,
            sticky="w",
            padx=5,
            pady=5,
        )
        self.chat_throttle_var = tk.IntVar(
            value=int(
                float(str(self.parent.preferences["chat_update_throttle"])) * 1000,
            ),
        )
        throttle_spin = ttk.Spinbox(
            frame,
            from_=50,
            to=500,
            increment=50,
            textvariable=self.chat_throttle_var,
            width=10,
        )
        throttle_spin.grid(row=3, column=1, padx=5, pady=5, sticky="w")

        # Help text - Move to row 4 to avoid overlap
        help_text = tk.Text(frame, height=8, width=60, wrap=tk.WORD)
        help_text.grid(row=4, column=0, columnspan=2, padx=5, pady=10, sticky="ew")
        help_text.insert(
            "1.0",
            "Settings Help:\n\n"
            "• Auto-save sessions: Automatically save your chat history when closing\n"
            "• UI Update Interval: How often the interface refreshes (lower = more responsive)\n"
            "• Max Undo Levels: -1 for unlimited, 0 to disable undo, positive number for limit\n"
            "• Chat Update Throttle: Minimum time between chat display updates (higher = less jumping)\n"
            "• Window Geometry: Format is 'WIDTHxHEIGHT+X_OFFSET+Y_OFFSET'\n"
            "• Background Color: Choose between black or white background\n"
            "• API URL: The endpoint for your Ollama server\n"
            "• Models: Specify which models to use for chat and summaries",
        )
        help_text.config(state=tk.DISABLED)

        frame.columnconfigure(1, weight=1)

    def test_connection(self) -> None:
        """Test the API connection with current settings."""
        test_url = self.api_url_var.get()
        try:
            # Try to make a simple request to test connectivity
            response = requests.get(
                test_url.replace("/api/chat", "/api/tags"),
                timeout=5,
            )
            if response.status_code == 200:
                messagebox.showinfo("Connection Test", "Connection successful!")
            else:
                messagebox.showerror(
                    "Connection Test",
                    f"Connection failed with status code: {response.status_code}",
                )
        except requests.exceptions.RequestException as e:
            messagebox.showerror("Connection Test", f"Connection failed: {str(e)}")

    def get_current_values(self) -> dict[str, Any]:
        """Get current values from the preference widgets."""
        return {
            "api_url": self.api_url_var.get(),
            "default_model": self.default_model_var.get(),
            "summary_model": self.summary_model_var.get(),
            "font_family": self.font_family_var.get(),
            "font_size": self.font_size_var.get(),
            "background_color": self.background_color_var.get(),  # Add this line
            "theme": self.theme_var.get(),
            "window_geometry": self.window_geometry_var.get(),
            "auto_save": self.auto_save_var.get(),
            "ui_update_interval": self.ui_update_var.get(),
            "max_undo_levels": self.max_undo_var.get(),
            "chat_update_throttle": self.chat_throttle_var.get()
            / 1000.0,  # Convert ms to seconds
        }

    def on_reset_defaults(self) -> None:
        """Reset all preferences to default values."""
        if messagebox.askyesno(
            "Reset Preferences",
            "Are you sure you want to reset all preferences to default values?",
        ):
            # Update all the variables with default values
            self.api_url_var.set(str(DEFAULT_PREFERENCES.get("api_url", "")))
            self.default_model_var.set(
                str(DEFAULT_PREFERENCES.get("default_model", "")),
            )
            self.summary_model_var.set(
                str(DEFAULT_PREFERENCES.get("summary_model", "")),
            )
            self.font_family_var.set(
                str(DEFAULT_PREFERENCES.get("font_family", "Cascadia Mono")),
            )
            self.font_size_var.set(int(str(DEFAULT_PREFERENCES.get("font_size", 12))))
            self.background_color_var.set(
                str(DEFAULT_PREFERENCES.get("background_color", "black")),
            )
            self.theme_var.set(str(DEFAULT_PREFERENCES.get("theme", "default")))
            self.window_geometry_var.set(
                str(DEFAULT_PREFERENCES.get("window_geometry", "600x800+100+100")),
            )
            self.auto_save_var.set(bool(DEFAULT_PREFERENCES.get("auto_save", True)))
            self.ui_update_var.set(
                int(str(DEFAULT_PREFERENCES.get("ui_update_interval", 500))),
            )
            self.max_undo_var.set(
                int(str(DEFAULT_PREFERENCES.get("max_undo_levels", -1))),
            )
            throttle_default = float(
                str(
                    DEFAULT_PREFERENCES.get("chat_update_throttle", 0.1),
                ),
            )
            self.chat_throttle_var.set(int(throttle_default * 1000))

    def on_ok(self) -> None:
        """Apply changes and close window."""
        self.apply_preferences()
        self.window.destroy()

    def on_cancel(self) -> None:
        """Cancel changes and close window."""
        # Restore original preferences
        self.parent.preferences = self.original_prefs.copy()
        self.parent.apply_appearance_preferences(self.parent.preferences)
        self.window.destroy()

    def on_apply(self) -> None:
        """Apply changes without closing window."""
        self.apply_preferences()

    def apply_preferences(self) -> None:
        """Apply the current preference values."""
        new_prefs = self.get_current_values()
        self.parent.preferences.update(new_prefs)
        self.parent.apply_preferences()
        self.parent.save_preferences()
