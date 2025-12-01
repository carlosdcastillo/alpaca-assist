#!/usr/bin/env python3
"""
Simple test script to demonstrate TRAMP filename functionality.
"""
from tramp_handler import get_tramp_handler


def test_tramp_parsing():
    """Test TRAMP filename parsing."""
    print("Testing TRAMP filename parsing...")

    try:
        tramp = get_tramp_handler()

        # Test valid TRAMP paths
        test_paths = [
            "/ssh:user@example.com:/home/user/file.txt",
            "/scp:alice@server.org:/var/log/app.log",
            "/ssh:bob@192.168.1.100:/etc/config.conf",
        ]

        for path in test_paths:
            if tramp.is_tramp_path(path):
                parsed = tramp.parse_tramp_path(path)
                print(f"✓ Parsed: {path}")
                print(f"  Method: {parsed.method}")
                print(f"  User: {parsed.user}")
                print(f"  Host: {parsed.host}")
                print(f"  Path: {parsed.path}")
                print()
            else:
                print(f"✗ Not recognized as TRAMP: {path}")

        # Test local paths
        local_paths = [
            "/Users/alice/documents/file.txt",
            "relative/path/file.py",
            "simple_file.md",
        ]

        for path in local_paths:
            is_tramp = tramp.is_tramp_path(path)
            print(f"Local path '{path}' is TRAMP: {is_tramp}")

    except RuntimeError as e:
        print(f"SSH not available: {e}")
        print("TRAMP functionality will be limited to local files only.")


def test_ssh_detection():
    """Test SSH client detection."""
    print("\nTesting SSH client detection...")

    try:
        tramp = get_tramp_handler()
        print("✓ SSH client detected and TRAMP handler initialized successfully")
    except RuntimeError as e:
        print(f"✗ SSH client detection failed: {e}")


if __name__ == "__main__":
    test_ssh_detection()
    test_tramp_parsing()

    print("\nTRAMP handler is ready!")
    print("Example usage:")
    print("  Local file: /Users/alice/file.txt")
    print("  Remote file: /ssh:alice@server.com:/home/alice/file.txt")
