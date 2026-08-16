"""
A Python wrapper for Mermaid CLI (mmdc) command-line tool.

This module provides a clean interface to the Mermaid diagram generator
for creating diagrams from Mermaid syntax.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal


class MermaidError(Exception):
    """Raised when mermaid command fails."""

    pass


class MermaidWrapper:
    """A wrapper around the Mermaid CLI (mmdc) command-line tool."""

    # Default puppeteer config path (in user's home directory)
    _DEFAULT_PUPPETEER_CONFIG = os.path.join(
        os.path.expanduser("~"),
        ".puppeteer-config.json",
    )

    # Default puppeteer config content - uses isolated user data dir to avoid
    # conflicts with running Chrome instances (especially on Windows)
    _DEFAULT_CONFIG_CONTENT = {
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--user-data-dir="
            + os.path.join(tempfile.gettempdir(), "puppeteer-mermaid-profile"),
        ],
    }

    @classmethod
    def _ensure_puppeteer_config(cls) -> str:
        """Ensure puppeteer config exists, creating with defaults if needed.

        Returns:
            Path to the config file.
        """
        config_path = cls._DEFAULT_PUPPETEER_CONFIG
        if not os.path.exists(config_path):
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cls._DEFAULT_CONFIG_CONTENT, f, indent=2)
        return config_path

    def __init__(
        self,
        mmdc_path: str = "mmdc",
        puppeteer_config_path: str | None = None,
        timeout: int = 120,
    ):
        """
        Initialize the mermaid wrapper.

        Args:
            mmdc_path: Path to the mmdc executable. Defaults to "mmdc" (assumes it's in PATH).
            puppeteer_config_path: Path to puppeteer config JSON file. If not provided,
                uses ~/.puppeteer-config.json (auto-created with sensible defaults if missing).
            timeout: Timeout in seconds for mmdc subprocess calls. Defaults to 120.

        Raises:
            MermaidError: If mmdc is not found or not executable.
        """
        self.timeout = timeout
        # Resolve full path using shutil.which() for cross-platform compatibility
        # On Windows, this resolves .CMD/.BAT wrappers that subprocess can't find otherwise
        resolved_path = shutil.which(mmdc_path)
        if resolved_path is None:
            raise MermaidError(
                f"mmdc not found at '{mmdc_path}'. "
                "Please ensure @mermaid-js/mermaid-cli is installed. "
                "Install with: npm install -g @mermaid-js/mermaid-cli",
            )
        self.mmdc_path = resolved_path

        # Set puppeteer config path (auto-create default if needed)
        if puppeteer_config_path is not None:
            self.puppeteer_config_path = puppeteer_config_path
        else:
            self.puppeteer_config_path = self._ensure_puppeteer_config()

        self._verify_mmdc_installed()

    def _verify_mmdc_installed(self) -> None:
        """
        Verify that mmdc is installed and accessible.

        Raises:
            MermaidError: If mmdc cannot be found or executed.
        """
        try:
            subprocess.run(
                [self.mmdc_path, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,  # Version check should be quick
                stdin=subprocess.DEVNULL,  # Prevent stdin conflicts with MCP server
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise MermaidError(
                f"mmdc not found at '{self.mmdc_path}'. "
                "Please ensure @mermaid-js/mermaid-cli is installed. "
                "Install with: npm install -g @mermaid-js/mermaid-cli",
            ) from e

    def render_from_file(
        self,
        input_file: str,
        output_format: Literal["svg", "png", "pdf"] = "png",
        theme: Literal["default", "forest", "dark", "neutral"] = "default",
        background_color: str | None = None,
        width: int | None = None,
        height: int | None = None,
        scale: int | None = None,
        output_path: str | None = None,
    ) -> tuple[bytes, str]:
        """
        Render a Mermaid diagram from an input file.

        Args:
            input_file: Path to the input file containing Mermaid diagram code (.mmd or .mermaid).
            output_format: Output format - "svg", "png", or "pdf".
            theme: Mermaid theme - "default", "forest", "dark", or "neutral".
            background_color: Background color (e.g., "transparent", "#ffffff").
            width: Output width in pixels.
            height: Output height in pixels.
            scale: Scale factor for the output.
            output_path: Optional path to save the output file. If not provided,
                        returns the content as bytes.

        Returns:
            Tuple of (content_bytes, output_file_path).
            If output_path is provided, content_bytes will be the file content.
            If output_path is not provided, a temporary file path is returned.

        Raises:
            MermaidError: If the render command fails.
            ValueError: If invalid parameters are provided.
            FileNotFoundError: If the input file does not exist.
        """
        # Validate input file exists
        if not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        if not os.path.isfile(input_file):
            raise ValueError(f"Input path is not a file: {input_file}")

        # Validate output format
        if output_format not in ("svg", "png", "pdf"):
            raise ValueError(
                f"Invalid output format: {output_format}. Must be svg, png, or pdf",
            )

        # Determine output path
        if output_path:
            out_path = output_path
        else:
            # Create temporary output file
            fd, out_path = tempfile.mkstemp(suffix=f".{output_format}")
            os.close(fd)

        # Build command
        cmd = [
            self.mmdc_path,
            "-i",
            input_file,
            "-o",
            out_path,
            "-e",
            output_format,
            "-t",
            theme,
        ]

        # Add puppeteer config if available
        if self.puppeteer_config_path:
            cmd.extend(["-p", self.puppeteer_config_path])

        # Add optional parameters
        if background_color:
            cmd.extend(["-b", background_color])
        if width:
            cmd.extend(["-w", str(width)])
        if height:
            cmd.extend(["-H", str(height)])
        if scale:
            cmd.extend(["-s", str(scale)])

        # Run mmdc
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,  # Prevent stdin conflicts with MCP server
            )
        except subprocess.TimeoutExpired:
            raise MermaidError(
                f"mmdc command timed out after {self.timeout} seconds. "
                "Try increasing the timeout or check if Chrome/Puppeteer is responding.",
            )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise MermaidError(
                f"mmdc command failed with exit code {result.returncode}: {error_msg}",
            )

        # Read the output file
        with open(out_path, "rb") as f:
            content = f.read()

        return content, out_path

    def render(
        self,
        mermaid_code: str,
        output_format: Literal["svg", "png", "pdf"] = "png",
        theme: Literal["default", "forest", "dark", "neutral"] = "default",
        background_color: str | None = None,
        width: int | None = None,
        height: int | None = None,
        scale: int | None = None,
        output_path: str | None = None,
    ) -> tuple[bytes, str]:
        """
        Render a Mermaid diagram from code.

        Args:
            mermaid_code: The Mermaid diagram code to render.
            output_format: Output format - "svg", "png", or "pdf".
            theme: Mermaid theme - "default", "forest", "dark", or "neutral".
            background_color: Background color (e.g., "transparent", "#ffffff").
            width: Output width in pixels.
            height: Output height in pixels.
            scale: Scale factor for the output.
            output_path: Optional path to save the output file. If not provided,
                        returns the content as bytes.

        Returns:
            Tuple of (content_bytes, output_file_path).
            If output_path is provided, content_bytes will be the file content.
            If output_path is not provided, a temporary file path is returned.

        Raises:
            MermaidError: If the render command fails.
            ValueError: If invalid parameters are provided.
        """
        if not mermaid_code.strip():
            raise ValueError("Mermaid code cannot be empty")

        # Validate output format
        if output_format not in ("svg", "png", "pdf"):
            raise ValueError(
                f"Invalid output format: {output_format}. Must be svg, png, or pdf",
            )

        # Create temporary input file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".mmd",
            delete=False,
            encoding="utf-8",
        ) as input_file:
            input_file.write(mermaid_code)
            input_path = input_file.name

        try:
            # Determine output path
            if output_path:
                out_path = output_path
            else:
                # Create temporary output file
                fd, out_path = tempfile.mkstemp(suffix=f".{output_format}")
                os.close(fd)

            # Build command
            cmd = [
                self.mmdc_path,
                "-i",
                input_path,
                "-o",
                out_path,
                "-e",
                output_format,
                "-t",
                theme,
            ]

            # Add puppeteer config if available
            if self.puppeteer_config_path:
                cmd.extend(["-p", self.puppeteer_config_path])

            # Add optional parameters
            if background_color:
                cmd.extend(["-b", background_color])
            if width:
                cmd.extend(["-w", str(width)])
            if height:
                cmd.extend(["-H", str(height)])
            if scale:
                cmd.extend(["-s", str(scale)])

            # Run mmdc
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.timeout,
                    stdin=subprocess.DEVNULL,  # Prevent stdin conflicts with MCP server
                )
            except subprocess.TimeoutExpired:
                raise MermaidError(
                    f"mmdc command timed out after {self.timeout} seconds. "
                    "Try increasing the timeout or check if Chrome/Puppeteer is responding.",
                )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise MermaidError(
                    f"mmdc command failed with exit code {result.returncode}: {error_msg}",
                )

            # Read the output file
            with open(out_path, "rb") as f:
                content = f.read()

            return content, out_path

        finally:
            # Clean up input file
            try:
                os.unlink(input_path)
            except OSError:
                pass

    def render_to_base64(
        self,
        mermaid_code: str,
        output_format: Literal["svg", "png", "pdf"] = "png",
        theme: Literal["default", "forest", "dark", "neutral"] = "default",
        background_color: str | None = None,
        width: int | None = None,
        height: int | None = None,
        scale: int | None = None,
    ) -> str:
        """
        Render a Mermaid diagram and return as base64-encoded string.

        Args:
            mermaid_code: The Mermaid diagram code to render.
            output_format: Output format - "svg", "png", or "pdf".
            theme: Mermaid theme - "default", "forest", "dark", or "neutral".
            background_color: Background color (e.g., "transparent", "#ffffff").
            width: Output width in pixels.
            height: Output height in pixels.
            scale: Scale factor for the output.

        Returns:
            Base64-encoded string of the rendered diagram.

        Raises:
            MermaidError: If the render command fails.
        """
        content, temp_path = self.render(
            mermaid_code=mermaid_code,
            output_format=output_format,
            theme=theme,
            background_color=background_color,
            width=width,
            height=height,
            scale=scale,
        )

        # Clean up temporary file
        try:
            os.unlink(temp_path)
        except OSError:
            pass

        return base64.b64encode(content).decode("utf-8")

    def validate(self, mermaid_code: str) -> tuple[bool, str]:
        """
        Validate Mermaid code without generating output.

        Args:
            mermaid_code: The Mermaid diagram code to validate.

        Returns:
            Tuple of (is_valid, error_message).
            If valid, error_message will be empty.
        """
        try:
            # Try to render to a temporary file to validate
            content, temp_path = self.render(mermaid_code, output_format="svg")
            # Clean up
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return True, ""
        except MermaidError as e:
            return False, str(e)
        except ValueError as e:
            return False, str(e)


def main(
    mermaid_code: str,
    output_path: str | None = None,
    output_format: Literal["svg", "png", "pdf"] = "png",
    theme: Literal["default", "forest", "dark", "neutral"] = "default",
    background_color: str | None = None,
    width: int | None = None,
    height: int | None = None,
    scale: int | None = None,
    return_base64: bool = False,
) -> str | bytes:
    """
    Main entry point for mermaid wrapper.

    Args:
        mermaid_code: The Mermaid diagram code to render.
        output_path: Optional path to save the output file.
        output_format: Output format - "svg", "png", or "pdf".
        theme: Mermaid theme - "default", "forest", "dark", or "neutral".
        background_color: Background color (e.g., "transparent", "#ffffff").
        width: Output width in pixels.
        height: Output height in pixels.
        scale: Scale factor for the output.
        return_base64: If True, return base64-encoded string instead of bytes.

    Returns:
        If return_base64 is True, returns base64-encoded string.
        If output_path is provided, returns the output path.
        Otherwise, returns the raw bytes.

    Raises:
        MermaidError: If mmdc is not found or render fails.
        ValueError: If invalid parameters are provided.

    Example:
        >>> result = main(
        ...     mermaid_code="graph TD; A-->B; B-->C;",
        ...     output_format="png",
        ...     theme="forest"
        ... )
    """
    wrapper = MermaidWrapper()

    if return_base64:
        return wrapper.render_to_base64(
            mermaid_code=mermaid_code,
            output_format=output_format,
            theme=theme,
            background_color=background_color,
            width=width,
            height=height,
            scale=scale,
        )
    else:
        content, path = wrapper.render(
            mermaid_code=mermaid_code,
            output_format=output_format,
            theme=theme,
            background_color=background_color,
            width=width,
            height=height,
            scale=scale,
            output_path=output_path,
        )

        if output_path:
            return output_path
        else:
            return content


if __name__ == "__main__":
    # Example usage
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Python wrapper for Mermaid CLI (mmdc)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Render a flowchart to PNG
  python mermaid_wrapper.py -c "graph TD; A-->B; B-->C;" -o diagram.png

  # Render from a file with dark theme
  python mermaid_wrapper.py -f diagram.mmd -o output.svg -e svg -t dark

  # Validate mermaid code
  python mermaid_wrapper.py -c "graph TD; A-->B;" --validate
        """,
    )

    parser.add_argument(
        "-c",
        "--code",
        help="Mermaid code to render",
    )
    parser.add_argument(
        "-f",
        "--file",
        help="Path to file containing Mermaid code",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path",
    )
    parser.add_argument(
        "-e",
        "--format",
        choices=["svg", "png", "pdf"],
        default="png",
        help="Output format (default: png)",
    )
    parser.add_argument(
        "-t",
        "--theme",
        choices=["default", "forest", "dark", "neutral"],
        default="default",
        help="Mermaid theme (default: default)",
    )
    parser.add_argument(
        "-b",
        "--background",
        help="Background color (e.g., 'transparent', '#ffffff')",
    )
    parser.add_argument(
        "-w",
        "--width",
        type=int,
        help="Output width in pixels",
    )
    parser.add_argument(
        "-H",
        "--height",
        type=int,
        help="Output height in pixels",
    )
    parser.add_argument(
        "-s",
        "--scale",
        type=int,
        help="Scale factor",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Only validate the mermaid code without generating output",
    )
    parser.add_argument(
        "--base64",
        action="store_true",
        help="Output as base64-encoded string",
    )

    args = parser.parse_args()

    # Get mermaid code from argument or file
    if args.code:
        mermaid_code = args.code
    elif args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                mermaid_code = f.read()
        except FileNotFoundError:
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Error: Either --code or --file must be provided", file=sys.stderr)
        sys.exit(1)

    try:
        wrapper = MermaidWrapper()

        if args.validate:
            is_valid, error = wrapper.validate(mermaid_code)
            if is_valid:
                print("✅ Mermaid code is valid")
                sys.exit(0)
            else:
                print(f"❌ Invalid Mermaid code: {error}", file=sys.stderr)
                sys.exit(1)

        if args.base64:
            result = wrapper.render_to_base64(
                mermaid_code=mermaid_code,
                output_format=args.format,
                theme=args.theme,
                background_color=args.background,
                width=args.width,
                height=args.height,
                scale=args.scale,
            )
            print(result)
        else:
            if not args.output:
                print(
                    "Error: --output is required when not using --base64",
                    file=sys.stderr,
                )
                sys.exit(1)

            content, path = wrapper.render(
                mermaid_code=mermaid_code,
                output_format=args.format,
                theme=args.theme,
                background_color=args.background,
                width=args.width,
                height=args.height,
                scale=args.scale,
                output_path=args.output,
            )
            print(f"✅ Diagram saved to: {path}")

    except (MermaidError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
