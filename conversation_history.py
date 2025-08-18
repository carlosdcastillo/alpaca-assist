import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import messagebox
from tkinter import ttk
from typing import List
from typing import Optional
from typing import Tuple

from database import ConversationDatabase


class ConversationHistoryWindow:
    def __init__(self, parent_app, master: tk.Tk):
        self.parent_app = parent_app
        self.master = master
        self.db = ConversationDatabase()

        self.window = tk.Toplevel(master)
        self.window.title("Conversation History")
        self.window.geometry("800x600")
        self.window.transient(master)
        self.window.grab_set()

        # Center on parent window
        self.center_window()

        self.create_widgets()
        self.load_conversations()

        # Bind close event
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)

    def center_window(self) -> None:
        """Center the window on the parent."""
        self.master.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() // 2) - 400
        y = self.master.winfo_y() + (self.master.winfo_height() // 2) - 300
        self.window.geometry(f"800x600+{x}+{y}")

    def create_widgets(self) -> None:
        """Create the UI widgets."""
        # Search frame
        search_frame = ttk.Frame(self.window)
        search_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.search_entry.bind("<KeyRelease>", self.on_search)

        # Treeview for conversations
        tree_frame = ttk.Frame(self.window)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Create treeview with scrollbar
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("title", "created", "closed"),
            show="headings",
        )
        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        # Configure columns
        self.tree.heading("title", text="Title")
        self.tree.heading("created", text="Created")
        self.tree.heading("closed", text="Closed")

        self.tree.column("title", width=400, anchor="w")
        self.tree.column("created", width=150, anchor="center")
        self.tree.column("closed", width=150, anchor="center")

        # Pack treeview and scrollbar
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind double-click to revive conversation
        self.tree.bind("<Double-1>", self.on_revive_conversation)

        # Button frame
        button_frame = ttk.Frame(self.window)
        button_frame.pack(fill="x", padx=10, pady=5)

        ttk.Button(button_frame, text="Revive", command=self.revive_selected).pack(
            side="left",
            padx=5,
        )
        ttk.Button(button_frame, text="Delete", command=self.delete_selected).pack(
            side="left",
            padx=5,
        )
        ttk.Button(button_frame, text="Refresh", command=self.load_conversations).pack(
            side="left",
            padx=5,
        )
        ttk.Button(button_frame, text="Close", command=self.on_closing).pack(
            side="right",
            padx=5,
        )

        # Status label
        self.status_label = ttk.Label(self.window, text="")
        self.status_label.pack(fill="x", padx=10, pady=5)

    def load_conversations(self) -> None:
        """Load conversations from database into the treeview."""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Get conversations from database
        conversations = self.db.get_conversations()

        for (
            conv_id,
            title,
            created_date,
            closed_date,
            summary_generated,
        ) in conversations:
            # Format dates for display
            try:
                created_dt = datetime.fromisoformat(created_date)
                closed_dt = datetime.fromisoformat(closed_date)
                created_str = created_dt.strftime("%Y-%m-%d %H:%M")
                closed_str = closed_dt.strftime("%Y-%m-%d %H:%M")

                # Add indicator if conversation was updated (closed date is much later than created date)
                time_diff = closed_dt - created_dt
                if time_diff.total_seconds() > 300:  # More than 5 minutes difference
                    title = f"{title} (Updated)"

            except ValueError:
                created_str = created_date
                closed_str = closed_date

            # Insert into treeview
            self.tree.insert(
                "",
                "end",
                values=(title, created_str, closed_str),
                tags=(str(conv_id),),
            )

        # Update status
        self.status_label.config(text=f"Loaded {len(conversations)} conversations")

    def on_search(self, event=None) -> None:
        """Handle search input."""
        search_term = self.search_var.get().strip()

        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)

        if search_term:
            conversations = self.db.search_conversations(search_term)
        else:
            conversations = self.db.get_conversations()

        for (
            conv_id,
            title,
            created_date,
            closed_date,
            summary_generated,
        ) in conversations:
            # Format dates for display
            try:
                created_dt = datetime.fromisoformat(created_date)
                closed_dt = datetime.fromisoformat(closed_date)
                created_str = created_dt.strftime("%Y-%m-%d %H:%M")
                closed_str = closed_dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                created_str = created_date
                closed_str = closed_date

            # Insert into treeview
            self.tree.insert(
                "",
                "end",
                values=(title, created_str, closed_str),
                tags=(str(conv_id),),
            )

        # Update status
        self.status_label.config(text=f"Found {len(conversations)} conversations")

    def get_selected_conversation_id(self) -> int | None:
        """Get the ID of the selected conversation."""
        selection = self.tree.selection()
        if not selection:
            return None

        item = selection[0]
        tags = self.tree.item(item, "tags")
        if tags:
            return int(tags[0])
        return None

    def revive_selected(self) -> None:
        """Revive the selected conversation."""
        conv_id = self.get_selected_conversation_id()
        if conv_id is None:
            messagebox.showwarning(
                "No Selection",
                "Please select a conversation to revive.",
            )
            return

        self.revive_conversation(conv_id)

    def on_revive_conversation(self, event) -> None:
        """Handle double-click on conversation."""
        conv_id = self.get_selected_conversation_id()
        if conv_id is not None:
            self.revive_conversation(conv_id)

    def revive_conversation(self, conv_id: int) -> None:
        """Revive a conversation by creating a new tab with its data."""
        chat_data = self.db.get_conversation(conv_id)
        if chat_data is None:
            messagebox.showerror("Error", "Failed to load conversation data.")
            return

        # Get the conversation title
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            title = self.tree.item(item, "values")[0]
        else:
            title = "Revived Chat"

        # Create new tab in parent app
        self.parent_app.create_tab(title)

        # Load the conversation data into the new tab
        new_tab = self.parent_app.tabs[-1]
        new_tab.load_from_data(chat_data)
        new_tab.rebuild_display_from_state()

        # Close this window
        self.on_closing()

        # No success message - just silently complete the operation

    def delete_selected(self) -> None:
        """Delete the selected conversation."""
        conv_id = self.get_selected_conversation_id()
        if conv_id is None:
            messagebox.showwarning(
                "No Selection",
                "Please select a conversation to delete.",
            )
            return

        # Get conversation title for confirmation
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            title = self.tree.item(item, "values")[0]
        else:
            title = "this conversation"

        # Confirm deletion
        if messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete '{title}'?\n\nThis action cannot be undone.",
        ):
            if self.db.delete_conversation(conv_id):
                messagebox.showinfo("Success", "Conversation deleted successfully.")
                self.load_conversations()  # Refresh the list
            else:
                messagebox.showerror("Error", "Failed to delete conversation.")

    def on_closing(self) -> None:
        """Handle window closing."""
        self.window.destroy()
