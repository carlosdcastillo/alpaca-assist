import platform
import tkinter as tk
from typing import Optional


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tooltip: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)
        self.is_macos = platform.system() == "Darwin"

    def show_tooltip(self, event: Optional[tk.Event] = None) -> None:
        """Show the tooltip at the current cursor position."""
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        # Create the tooltip window
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        # Apply OS-specific settings
        if self.is_macos:
            try:
                # Set the window type to help it behave better on macOS
                self.tooltip.attributes("-type", "tooltip")
                self.tooltip.attributes("-alpha", 0.9)
            except tk.TclError:
                pass

        # Add the tooltip text
        label = tk.Label(
            self.tooltip,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
        )
        label.pack()

        # For macOS, bind both mouse enter and click events
        if self.is_macos:
            self.tooltip.bind("<Button-1>", self.click_through)
            label.bind("<Button-1>", self.click_through)
            self.tooltip.bind("<Enter>", self.hide_tooltip)
            label.bind("<Enter>", self.hide_tooltip)

    def click_through(self, event: Optional[tk.Event] = None) -> None:
        """Pass the click through to the underlying widget."""
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None
            # Schedule the click event slightly after hiding the tooltip
            self.widget.after(10, lambda: self.widget.event_generate("<Button-1>"))

    def hide_tooltip(self, event: Optional[tk.Event] = None) -> None:
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None
