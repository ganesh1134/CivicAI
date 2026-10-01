from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"


def main():
    PUBLIC_DIR.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "frontend" / "index.html", PUBLIC_DIR / "index.html")


if __name__ == "__main__":
    main()
