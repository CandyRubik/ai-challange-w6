"""Assemble a captioned video from real browser screenshots captured during the demo."""

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCENES = [
    ("00-chat.jpg", "Чат готов сразу · без профиля и интервью\nСохраняются сообщения, память, задачи и /help", 7),
    ("01-structure.jpg", "День 31 · /help объясняет структуру проекта\nРеальные ответы локальной Qwen · монтаж кадров интерфейса", 9),
    ("02-memory.jpg", "RAG: README + docs · ответ о хранении данных\nСсылки ведут к конкретным файлам, разделам и строкам", 9),
    ("02-source.jpg", "Проверяемый источник · docs/architecture.md\nСтроки документа и подсветка места, на которое ссылается ответ", 7),
    ("03-mcp.jpg", "MCP stdio · git_branch\nТекущая ветка, HEAD и время реального обращения к checkout", 9),
    ("04-negative.jpg", "Отрицательный вопрос · сведений недостаточно\nАссистент сообщает об отсутствии подтверждённых данных", 8),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, default=ROOT / "data/day31-video")
    parser.add_argument("--output", type=Path, default=ROOT / "data/day31-developer-assistant.mp4")
    parser.add_argument("--font", type=Path, default=Path("/System/Library/Fonts/Supplemental/Arial.ttf"))
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error("Укажите --font с поддержкой кириллицы")
    for filename, _, _ in SCENES:
        if not (args.frames / filename).is_file():
            parser.error(f"Не найден кадр: {filename}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, (filename, caption, duration) in enumerate(SCENES):
        caption_path = (args.frames / f"caption-{i}.png").resolve()
        title = Image.new("RGB", (1280, 80), "#0b0e15")
        draw = ImageDraw.Draw(title)
        draw.multiline_text((28, 10), caption, fill="white", font=ImageFont.truetype(str(args.font), 24), spacing=6)
        title.save(caption_path)
        clip = (args.frames / f"clip-{i}.mp4").resolve()
        video_filter = (
            "[0:v]scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x0b0e15[screen];"
            "[1:v][screen]vstack=inputs=2[video]"
        )
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "24",
                        "-i", str(args.frames / filename), "-loop", "1", "-framerate", "24", "-i", str(caption_path),
                        "-t", str(duration), "-filter_complex", video_filter, "-map", "[video]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip)], check=True)
        clips.append(clip)
    listing = (args.frames / "clips.txt").resolve()
    listing.write_text("\n".join("file '" + str(path).replace("'", "'\\''") + "'" for path in clips))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", "-movflags", "+faststart", str(args.output)], check=True)
    print(args.output)


if __name__ == "__main__":
    main()
