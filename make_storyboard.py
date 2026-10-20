import webvtt
from webvtt import WebVTT
from collections import defaultdict
import argparse
import cv2
import glob
import json
import os
import shutil
import sys
from PIL import Image
from tqdm import tqdm

DEFAULT_QUALITY_WEBP = 60
DEFAULT_QUALITY_JPG = 75

parser = argparse.ArgumentParser()
parser.add_argument("-v", "--verbose", action="store_true",
                    help="print a line per frame instead of showing a progress bar")
parser.add_argument("--input-video", help="video file (default: first .webm/.mp4 in the current directory)")
parser.add_argument("--input-subs", help="caption file, .vtt or .srt (default: first one in the current directory)")
parser.add_argument("--output-folder", default=".",
                    help="folder for the images, frames.js and index.html (default: current directory)")
parser.add_argument("--jpg", action="store_true", help="write JPG images instead of WebP")
parser.add_argument("--quality", type=int,
                    help=f"image quality, 1-100 "
                         f"(default: {DEFAULT_QUALITY_WEBP} for WebP, {DEFAULT_QUALITY_JPG} for JPG)")
parser.add_argument("--title", help="page title (default: the video's file name)")
parser.add_argument("--force", action="store_true",
                    help="overwrite output from a previous run in the output folder")
args = parser.parse_args()

if args.quality is None:
    args.quality = DEFAULT_QUALITY_JPG if args.jpg else DEFAULT_QUALITY_WEBP
if not 1 <= args.quality <= 100:
    parser.error("--quality must be between 1 and 100")

# Refuse to overwrite earlier output unless --force (only files this script writes are checked)
existing = [name for name in ("frames.js", "index.html")
            if os.path.exists(os.path.join(args.output_folder, name))]
for extension in ("jpg", "webp"):
    existing += [os.path.basename(path) for path in glob.glob(
        os.path.join(glob.escape(args.output_folder), f'[0-9][0-9][0-9][0-9][0-9][0-9].{extension}'))]
if existing and not args.force:
    parser.error(f"{args.output_folder} already has output from a previous run "
                 f"({len(existing)} files, e.g. {existing[0]}); use --force to overwrite it")

os.makedirs(args.output_folder, exist_ok=True)

if args.input_subs:
    sub_file = args.input_subs
else:
    sub_files = glob.glob('*.[sv][rt]t')
    print(sub_files)
    if not sub_files:
        parser.error("no .vtt/.srt file in the current directory; pass --input-subs")
    sub_file = sub_files[0]

# Check if the file is an srt file
if os.path.splitext(sub_file)[1] == '.srt':
    # Convert the srt file to a vtt file (saved in the output folder)
    vtt_file = WebVTT.from_srt(sub_file)
    sub_file = os.path.join(args.output_folder, os.path.splitext(os.path.basename(sub_file))[0] + '.vtt')
    vtt_file.save(sub_file)

# Open the WebVTT file
vtt = webvtt.read(sub_file)

# Create a dictionary with the start time as the key and the text as the value.
subtitles = defaultdict(list)
for caption in vtt:
    text=caption.text
    split_text=text.split(f"\n")
    for split in split_text:
        if split.strip() != "":
            subtitles[caption.start] = split.strip()


# Remove duplicate entries
subtitles_clean = {}
last = ""
for timestamp, sub in subtitles.items():
    if sub != last:
        subtitles_clean[timestamp] = sub
    last = sub


if args.input_video:
    video_file = args.input_video
else:
    video_files = glob.glob('*.[mw][ep][bm][4m]')
    print(video_files)
    if not video_files:
        parser.error("no .webm/.mp4 file in the current directory; pass --input-video")
    video_file = video_files[0]

cap = cv2.VideoCapture(video_file)
if not cap.isOpened():
    parser.error(f"could not open video: {video_file}")

i=0
skipped = 0

frames = []

entries = subtitles_clean.items()
if not args.verbose:
    entries = tqdm(entries, desc="frames", unit="frame")

for timestamp, sub in entries:
    if args.verbose:
        print(f"generating frame at {timestamp} with text \"{sub}\".")
    # Convert the timestamp to milliseconds
    hours, minutes, seconds = map(float, timestamp.split(':'))
    milliseconds = int((hours * 3600 + minutes * 60 + seconds) * 1000)

    # Set the frame position to the timestamp
    cap.set(cv2.CAP_PROP_POS_MSEC, milliseconds)

    # Read the frame
    ret, frame = cap.read()
    if not ret:
        # e.g. a caption timestamp past the end of the video: report it and carry on without this frame
        skipped += 1
        tqdm.write(f"ERROR: could not read a frame at {timestamp} (caption \"{sub}\"); skipping it.", file=sys.stderr)
        continue
    i+=1

    if frame.shape[1] >= 720:
        # Get the aspect ratio
        aspect_ratio = frame.shape[1] / frame.shape[0]

        # Calculate the height
        height = int(720 / aspect_ratio)

        # Resize the image
        frame = cv2.resize(frame, (720, height))

    frame_height, frame_width = frame.shape[:2]
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    image_name = f'{i:06}.jpg' if args.jpg else f'{i:06}.webp'
    image_path = os.path.join(args.output_folder, image_name)
    if args.jpg:
        # Save the frame as an optimized progressive JPG
        image.save(image_path, 'JPEG', quality=args.quality, optimize=True, progressive=True)
    else:
        image.save(image_path, 'WEBP', quality=args.quality, method=6)

    frames.append({
        "image": image_name,
        "width": frame_width,
        "height": frame_height,
        "timestamp": timestamp,
        "caption": sub,
    })
cap.release()
if skipped:
    print(f"WARNING: {skipped} caption(s) had no readable frame and were left out ({len(frames)} frames written).",
          file=sys.stderr)

# frames.js is a plain script (not fetched) so index.html works when opened directly from disk
with open(os.path.join(args.output_folder, "frames.js"), "w", encoding="utf-8") as f:
    title = args.title if args.title is not None else os.path.splitext(os.path.basename(video_file))[0]
    f.write("const TITLE = ")
    json.dump(title, f, ensure_ascii=False)
    f.write(";\nconst FRAMES = ")
    json.dump(frames, f, ensure_ascii=False, indent=1)
    f.write(";\n")

shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.html"),
                os.path.join(args.output_folder, "index.html"))
