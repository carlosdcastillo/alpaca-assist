import asyncio
import json
import os
import threading
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from typing import Any
from typing import Dict
from typing import List


class MCPConfigWindow:
    def __init__(self, parent, master: tk.Tk):
        self.parent = parent
        self.master = master
        self.window = tk.Toplevel(master)
        self.window.title("MCP Server Configuration")
        self.window.geometry("600x500")
        self.window.transient(master)
        self.window.grab_set()

        # Center on parent
        master.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() // 2) - 300
        y = master.winfo_y() + (master.winfo_height() // 2) - 250
        self.window.geometry(f"600x500+{x}+{y}")

        self.create_widgets()
        self.load_server_configs()

    def create_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Server list frame
        list_frame = ttk.LabelFrame(main_frame, text="MCP Servers")
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        # Server listbox with scrollbar
        list_container = ttk.Frame(list_frame)
        list_container.pack(fill="both", expand=True, padx=5, pady=5)

        self.server_listbox = tk.Listbox(list_container, height=8)
        scrollbar = ttk.Scrollbar(
            list_container,
            orient="vertical",
            command=self.server_listbox.yview,
        )
        self.server_listbox.configure(yscrollcommand=scrollbar.set)

        self.server_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Server config frame
        config_frame = ttk.LabelFrame(main_frame, text="Server Configuration")
        config_frame.pack(fill="x", pady=(0, 10))

        # Name
        ttk.Label(config_frame, text="Name:").grid(
            row=0,
            column=0,
            sticky="w",
            padx=5,
            pady=2,
        )
        self.name_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.name_var, width=40).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=5,
            pady=2,
        )

        # Command
        ttk.Label(config_frame, text="Command:").grid(
            row=1,
            column=0,
            sticky="w",
            padx=5,
            pady=2,
        )
        self.command_var = tk.StringVar()
        command_frame = ttk.Frame(config_frame)
        command_frame.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        ttk.Entry(command_frame, textvariable=self.command_var).pack(
            side="left",
            fill="x",
            expand=True,
        )
        ttk.Button(
            command_frame,
            text="Browse",
            command=self.browse_command,
            width=8,
        ).pack(side="right", padx=(5, 0))

        # Arguments
        ttk.Label(config_frame, text="Arguments:").grid(
            row=2,
            column=0,
            sticky="w",
            padx=5,
            pady=2,
        )
        self.args_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.args_var, width=40).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=5,
            pady=2,
        )

        config_frame.columnconfigure(1, weight=1)

        # Buttons frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x")

        ttk.Button(button_frame, text="Add Server", command=self.add_server).pack(
            side="left",
            padx=(0, 5),
        )
        ttk.Button(button_frame, text="Update Server", command=self.update_server).pack(
            side="left",
            padx=(0, 5),
        )
        ttk.Button(button_frame, text="Remove Server", command=self.remove_server).pack(
            side="left",
            padx=(0, 5),
        )
        ttk.Button(
            button_frame,
            text="Test Connection",
            command=self.test_connection,
        ).pack(side="left", padx=(0, 5))

        # Close button
        ttk.Button(button_frame, text="Close", command=self.window.destroy).pack(
            side="right",
        )

        # Bind listbox selection
        self.server_listbox.bind("<<ListboxSelect>>", self.on_server_select)

    def browse_command(self):
        filename = filedialog.askopenfilename(
            title="Select MCP Server Executable",
            filetypes=[
                ("Python files", "*.py"),
                ("Executables", "*.exe"),
                ("All files", "*.*"),
            ],
        )
        if filename:
            self.command_var.set(filename)

    def load_server_configs(self):
        """Load server configurations from file."""
        try:
            if os.path.exists("mcp_servers.json"):
                with open("mcp_servers.json") as f:
                    configs = json.load(f)
                    for name, config in configs.items():
                        self.server_listbox.insert(tk.END, name)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load server configs: {e}")

    def save_server_configs(self):
        """Save server configurations to file."""
        try:
            configs = {}
            if hasattr(self.parent, "mcp_manager"):
                configs = self.parent.mcp_manager.server_configs

            with open("mcp_servers.json", "w") as f:
                json.dump(configs, f, indent=2)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save server configs: {e}")

    def on_server_select(self, event):
        """Handle server selection."""
        selection = self.server_listbox.curselection()
        if selection:
            server_name = self.server_listbox.get(selection[0])
            if (
                hasattr(self.parent, "mcp_manager")
                and server_name in self.parent.mcp_manager.server_configs
            ):
                config = self.parent.mcp_manager.server_configs[server_name]
                self.name_var.set(server_name)
                self.command_var.set(" ".join(config.get("command", [])))
                self.args_var.set(" ".join(config.get("args", [])))

    def add_server(self):
        """Add a new MCP server."""
        name = self.name_var.get().strip()
        command = self.command_var.get().strip()
        args = self.args_var.get().strip()

        if not name or not command:
            messagebox.showerror("Error", "Name and Command are required")
            return

        # Parse command and args
        command_parts = command.split()
        args_parts = args.split() if args else []

        # Add to parent's MCP manager
        if not hasattr(self.parent, "mcp_manager"):
            messagebox.showerror("Error", "MCP Manager not initialized")
            return

        # Store config
        self.parent.mcp_manager.server_configs[name] = {
            "command": command_parts,
            "args": args_parts,
        }

        # Add to listbox if not already there
        if name not in [
            self.server_listbox.get(i) for i in range(self.server_listbox.size())
        ]:
            self.server_listbox.insert(tk.END, name)

        self.save_server_configs()
        messagebox.showinfo("Success", f"Server '{name}' added successfully")

        # Clear fields
        self.name_var.set("")
        self.command_var.set("")
        self.args_var.set("")

    def update_server(self):
        """Update selected server."""
        selection = self.server_listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Please select a server to update")
            return

        old_name = self.server_listbox.get(selection[0])
        new_name = self.name_var.get().strip()
        command = self.command_var.get().strip()
        args = self.args_var.get().strip()

        if not new_name or not command:
            messagebox.showerror("Error", "Name and Command are required")
            return

        # Parse command and args
        command_parts = command.split()
        args_parts = args.split() if args else []

        # Update config
        if old_name != new_name:
            # Remove old config
            if old_name in self.parent.mcp_manager.server_configs:
                del self.parent.mcp_manager.server_configs[old_name]
            # Update listbox
            self.server_listbox.delete(selection[0])
            self.server_listbox.insert(selection[0], new_name)

        self.parent.mcp_manager.server_configs[new_name] = {
            "command": command_parts,
            "args": args_parts,
        }

        self.save_server_configs()
        messagebox.showinfo("Success", f"Server '{new_name}' updated successfully")

    def remove_server(self):
        """Remove selected server."""
        selection = self.server_listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Please select a server to remove")
            return

        server_name = self.server_listbox.get(selection[0])

        if messagebox.askyesno("Confirm", f"Remove server '{server_name}'?"):
            # Remove from config
            if server_name in self.parent.mcp_manager.server_configs:
                del self.parent.mcp_manager.server_configs[server_name]

            # Remove from listbox
            self.server_listbox.delete(selection[0])

            # Disconnect if connected
            if hasattr(self.parent, "mcp_manager"):
                asyncio.run_coroutine_threadsafe(
                    self.parent.mcp_manager.disconnect_server(server_name),
                    self.parent.event_loop,
                )

            self.save_server_configs()
            messagebox.showinfo(
                "Success",
                f"Server '{server_name}' removed successfully",
            )

            # Clear fields
            self.name_var.set("")
            self.command_var.set("")
            self.args_var.set("")

    def test_connection(self):
        """Test connection to selected server."""
        name = self.name_var.get().strip()
        command = self.command_var.get().strip()
        args = self.args_var.get().strip()

        if not name or not command:
            messagebox.showerror("Error", "Name and Command are required")
            return

        # Parse command and args
        command_parts = command.split()
        args_parts = args.split() if args else []

        # Test connection in a separate thread
        def test_connection_async():
            try:
                from mcp_manager import MCPManager

                test_manager = MCPManager()

                async def test():
                    success = await test_manager.add_server(
                        name,
                        command_parts,
                        args_parts,
                    )
                    if success:
                        tools = test_manager.get_available_tools()
                        await test_manager.disconnect_server(name)
                        return True, tools.get(name, [])
                    return False, []

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, tools = loop.run_until_complete(test())
                loop.close()

                if success:
                    self.master.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Success",
                            f"Connection successful!\nFound {len(tools)} tools.",
                        ),
                    )
                else:
                    self.master.after(
                        0,
                        lambda: messagebox.showerror(
                            "Error",
                            "Failed to connect to server",
                        ),
                    )

            except Exception as e:
                self.master.after(
                    0,
                    lambda: messagebox.showerror(
                        "Error",
                        f"Connection test failed: {e}",
                    ),
                )

        threading.Thread(target=test_connection_async, daemon=True).start()
