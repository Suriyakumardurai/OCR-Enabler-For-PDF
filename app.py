from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import shutil, os, uuid, asyncio
import ocrmypdf
from zipfile import ZipFile

# Setup
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Helper: run OCR asynchronously
async def run_ocr(input_path, output_path, lang="eng"):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: ocrmypdf.ocr(
        input_path,
        output_path,
        language=lang,
        deskew=True,
        clean=False,
        rotate_pages=True,
        optimize=0,
        remove_background=False,
        force_ocr=True
    ))

# Home page: upload form
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# Upload and process PDFs
# Upload and process PDFs (multithreaded per file)
@app.post("/upload", response_class=HTMLResponse)
async def upload_pdfs(
    request: Request, 
    files: list[UploadFile] = File(...), 
    lang: str = Form("eng")
):
    processed_files = []

    # Step 1: Save all uploaded PDFs first
    tasks = []
    for file in files:
        file_id = str(uuid.uuid4())
        upload_path = os.path.join(UPLOAD_DIR, f"{file_id}_{file.filename}")
        output_path = os.path.join(OUTPUT_DIR, f"ocr_{file.filename}")

        with open(upload_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Store task for OCR
        tasks.append((upload_path, output_path, lang))

    # Step 2: Run OCR on all files concurrently
    loop = asyncio.get_event_loop()
    await asyncio.gather(
        *[
            loop.run_in_executor(
                None,  # use default ThreadPoolExecutor
                lambda p=inp, o=out, l=lg: ocrmypdf.ocr(
                    p, o,
                    language=l,
                    deskew=True,            # straighten crooked scans
                    clean=True,             # clean up noisy backgrounds
                    clean_final=True,       # extra cleaning pass
                    rotate_pages=True,      # auto-rotate pages
                    optimize=0,             # no image compression, preserves quality
                    remove_background=False,# keep original background detail
                    force_ocr=True,         # force raster-to-text
                    output_type="pdfa-2",   # archival, higher compatibility
                    fast_web_view=0         # skip linearization step (saves time)
                )
            )
            for inp, out, lg in tasks
        ]
    )

    # Step 3: Collect results
    for _, output_path, _ in tasks:
        processed_files.append({"filename": os.path.basename(output_path)})

    return templates.TemplateResponse("results.html", {"request": request, "files": processed_files})


# Download a single PDF
@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename)
    return {"error": "File not found"}

# Bulk download (ZIP)
@app.get("/download-zip")
def download_zip():
    zip_name = "ocr_results.zip"
    zip_path = os.path.join(OUTPUT_DIR, zip_name)
    with ZipFile(zip_path, "w") as zipf:
        for f in os.listdir(OUTPUT_DIR):
            if f.endswith(".pdf") and f != zip_name:
                zipf.write(os.path.join(OUTPUT_DIR, f), arcname=f)
    return FileResponse(zip_path, filename=zip_name)
