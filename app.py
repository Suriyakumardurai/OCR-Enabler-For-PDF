from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, asyncio  # <-- MODIFIED: shutil and uuid removed
import ocrmypdf
from zipfile import ZipFile

# Setup
app = FastAPI(title="OCR Enabler Pro", description="Ultimate OCR PDF Processor", version="2.0")
# Note: You can remove the /static mount if you are not using it for anything else.
# For now, we'll assume it might be used for favicons, etc.
app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")
templates = Jinja2Templates(directory="templates")

# --- REMOVED UPLOAD_DIR ---
# No longer need to store raw uploads
OUTPUT_DIR = "outputs"
# The 'uploads' directory is no longer created
os.makedirs(OUTPUT_DIR, exist_ok=True)


# --- MODIFIED Helper: OCR execution ---
# Renamed 'input_path' to 'input_file' to reflect it can be a stream
async def run_ocr(input_file, output_path, lang="eng"):
    """
    Runs ocrmypdf on an input file stream or path.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: ocrmypdf.ocr(
            input_file,  # <-- MODIFIED: This can now be a file-like object
            output_path,
            language=lang,
            deskew=True,
            clean=True,
            clean_final=True,
            rotate_pages=True,
            optimize=0,
            remove_background=False,
            force_ocr=True,
            output_type="pdfa-2",
            fast_web_view=0
        )
    )


# Home page (no changes)
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- MODIFIED Upload PDFs Endpoint ---
@app.post("/upload")
async def upload_pdfs(
    request: Request,
    files: list[UploadFile] = File(...),
    lang: str = Form("eng")
):
    processed_filenames = []
    tasks = []

    try:
        for file in files:
            safe_filename = os.path.basename(file.filename)
            output_path = os.path.join(OUTPUT_DIR, f"ocr_{safe_filename}")

            # --- CORE CHANGE ---
            # We no longer save the uploaded file to disk.
            # We pass the in-memory file object (file.file) directly to the OCR function.
            # FastAPI handles temporary storage for large files automatically.
            task = run_ocr(file.file, output_path, lang)
            tasks.append(task)
            processed_filenames.append(os.path.basename(output_path))
        
        await asyncio.gather(*tasks)
        
        return JSONResponse(
            status_code=200,
            content={"success": True, "processed_files": processed_filenames}
        )
    except Exception as e:
        print(f"An error occurred during upload/OCR: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "An unexpected error occurred during processing."}
        )


# --- NO CHANGES to endpoints below this line ---

# Download single file
@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(
            file_path, 
            filename=filename, 
            content_disposition_type="inline"
        )
    return {"error": "File not found"}


# Get list of processed files
@app.get("/processed-files", response_class=JSONResponse)
async def get_processed_files():
    try:
        files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.pdf') and f != "ocr_results.zip"]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(OUTPUT_DIR, x)), reverse=True)
        return {"files": files}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# Download all results as ZIP
@app.get("/download-zip")
def download_zip():
    zip_name = "ocr_results.zip"
    zip_path = os.path.join(OUTPUT_DIR, zip_name)
    with ZipFile(zip_path, "w") as zipf:
        for f in os.listdir(OUTPUT_DIR):
            if f.endswith(".pdf") and f != zip_name:
                zipf.write(os.path.join(OUTPUT_DIR, f), arcname=f)
    return FileResponse(zip_path, filename=zip_name)