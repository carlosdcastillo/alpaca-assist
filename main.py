import os
import sys
import tkinter as tk

from PIL import Image
from PIL import ImageTk

from chat_app import ChatApp


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def main() -> None:
    root = tk.Tk()
    try:
        icon_path = resource_path("alpaca.png")
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
            img = img.resize((32, 32), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            root.iconphoto(True, photo)
            root._icon_photo = photo
    except Exception as e:
        print(f"Icon loading failed: {e}")

    app = ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
