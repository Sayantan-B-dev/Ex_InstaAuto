from pathlib import Path

OUTPUT_FILE = "tree.txt"

# Folders to ignore
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vscode",
}

# Files to ignore
IGNORE_FILES = {
    OUTPUT_FILE,
}


def tree(directory: Path, prefix=""):
    entries = sorted(
        [
            e for e in directory.iterdir()
            if e.name not in IGNORE_DIRS
            and e.name not in IGNORE_FILES
        ],
        key=lambda x: (x.is_file(), x.name.lower())
    )

    lines = []

    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        name = entry.name + ("/" if entry.is_dir() else "")
        lines.append(f"{prefix}{connector}{name}")

        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.extend(tree(entry, prefix + extension))

    return lines


def main():
    root = Path(".").resolve()

    lines = [root.name + "/"]
    lines.extend(tree(root))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Tree written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()