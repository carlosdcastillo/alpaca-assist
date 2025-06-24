import tkinter as tk
from typing import Optional


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tooltip: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event: Optional[tk.Event] = None) -> None:
        """Show the tooltip at the current cursor position."""
        # Get the position of the text cursor
        # Explicitly cast the return value to the expected tuple type or None
        bbox_result: Optional[tuple[int, int, int, int]] = self.widget.bbox("insert")  # type: ignore

        if not bbox_result:  # If bbox returns None
            return  # Exit the method early

        # Now that we know bbox_result is not None, we can safely unpack it
        x, y, _, _ = bbox_result

        # Position the tooltip window
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        # Create the tooltip window
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        # Add the tooltip text
        label = tk.Label(
            self.tooltip,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
        )
        label.pack()

    def hide_tooltip(self, event: Optional[tk.Event] = None) -> None:
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None
