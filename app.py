from fastapi import FastAPI, File, UploadFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import asyncio
import uuid
import ocrmypdf
from zipfile import ZipFile
import json
import io  # <-- MODIFICATION 1: Import the io library

# --- Setup ---
app = FastAPI(title="OCR Enabler Pro", description="Ultimate OCR PDF Processor", version="3.0")
app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")
templates = Jinja2Templates(directory="templates")
OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# In-memory dictionary to track progress of OCR jobs
job_progress = {}

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, job_id: str):
        await websocket.accept()
        self.active_connections[job_id] = websocket

    def disconnect(self, job_id: str):
        if job_id in self.active_connections:
            del self.active_connections[job_id]

    async def send_progress(self, job_id: str, message: dict):
        if job_id in self.active_connections:
            await self.active_connections[job_id].send_text(json.dumps(message))

manager = ConnectionManager()

# --- OCR Helper with Progress Reporting (No changes here) ---
async def run_ocr_with_progress(input_file, output_path, lang, job_id, total_files):
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: ocrmypdf.ocr(
                input_file, output_path, language=lang, deskew=True, clean=True, clean_final=True,
                rotate_pages=True, optimize=0, remove_background=False, force_ocr=True,
                output_type="pdfa-2", fast_web_view=0
            )
        )
        job_progress[job_id]["completed"] += 1
        progress = job_progress[job_id]["completed"]
        await manager.send_progress(job_id, {"type": "progress", "processed": progress, "total": total_files})
    except Exception as e:
        print(f"OCR Error for {output_path}: {e}")
        job_progress[job_id]["completed"] += 1
        progress = job_progress[job_id]["completed"]
        await manager.send_progress(job_id, { "type": "error", "message": f"Failed: {os.path.basename(output_path)}", "processed": progress, "total": total_files })

# --- WebSocket Endpoint (No changes here) ---
@app.websocket("/ws/progress/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await manager.connect(websocket, job_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(job_id)
        if job_id in job_progress:
             del job_progress[job_id]

# --- MODIFIED Upload Endpoint ---
@app.post("/upload")
async def upload_pdfs(files: list[UploadFile] = File(...), lang: str = Form("eng")):
    job_id = str(uuid.uuid4())
    total_files = len(files)
    job_progress[job_id] = {"completed": 0, "total": total_files}

    # --- MODIFICATION 2: Prepare a list to hold file data in memory ---
    job_data = []
    processed_filenames = []
    for file in files:
        # Read the file content into memory immediately
        content = await file.read()
        safe_filename = os.path.basename(file.filename)
        output_path = os.path.join(OUTPUT_DIR, f"ocr_{safe_filename}")
        
        job_data.append({
            "content": content,
            "output_path": output_path,
        })
        processed_filenames.append(os.path.basename(output_path))
    
    # This background task now works with the in-memory data
    async def run_ocr_tasks():
        tasks = []
        for data in job_data:
            # Create a BytesIO stream from the in-memory content
            input_stream = io.BytesIO(data["content"])
            
            task = run_ocr_with_progress(
                input_stream,  # Pass the new memory stream
                data["output_path"],
                lang,
                job_id,
                total_files
            )
            tasks.append(task)
            
        await asyncio.gather(*tasks)
        
        await manager.send_progress(job_id, {
            "type": "complete",
            "processed_files": processed_filenames
        })

    asyncio.create_task(run_ocr_tasks())
    
    return JSONResponse({"success": True, "job_id": job_id})


# --- Other Endpoints (no changes) ---
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, filename=filename, content_disposition_type="inline")
    return {"error": "File not found"}

@app.get("/processed-files", response_class=JSONResponse)
async def get_processed_files():
    try:
        files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.pdf') and f != "ocr_results.zip"]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(OUTPUT_DIR, x)), reverse=True)
        return {"files": files}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download-zip")
def download_zip():
    zip_name = "ocr_results.zip"
    zip_path = os.path.join(OUTPUT_DIR, zip_name)
    with ZipFile(zip_path, "w") as zipf:
        for f in os.listdir(OUTPUT_DIR):
            if f.endswith(".pdf") and f != zip_name:
                zipf.write(os.path.join(OUTPUT_DIR, f), arcname=f)
    return FileResponse(zip_path, filename=zip_name)