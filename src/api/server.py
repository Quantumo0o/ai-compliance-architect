from fastapi import FastAPI, BackgroundTasks, HTTPException
import asyncio
import httpx
from src.core.judge import AdjudicationManager
from src.utils.db_handler import DBHandler
from src.utils.logger import logger
import config

app = FastAPI(title="AI Compliance Architect API")
db = DBHandler()

# Global Async Queue for DB Writes to prevent SQLite locks
db_queue = asyncio.Queue()

async def db_writer_worker():
    """
    Consumer task that processes all database write requests sequentially.
    """
    logger.info("Database write worker started.")
    while True:
        try:
            # item is (func_name, args, kwargs)
            func_name, args, kwargs = await db_queue.get()
            
            # Execute the synchronous DB method in a thread
            func = getattr(db, func_name)
            await asyncio.to_thread(func, *args, **kwargs)
            
            db_queue.task_done()
        except Exception as e:
            logger.error(f"Database write worker error: {e}")

@app.on_event("startup")
async def startup_event():
    # Start the DB writer worker
    asyncio.create_task(db_writer_worker())
    
    # Startup Recovery: Resume interrupted tasks
    logger.info("Checking for interrupted tasks...")
    doc_ids = db.get_incomplete_tasks()
    for doc_id in doc_ids:
        logger.info(f"Resuming interrupted analysis for doc_id={doc_id}")
        asyncio.create_task(process_document_background(doc_id))

async def process_document_background(doc_id: int, portfolio: str = None):
    """
    Background worker that processes all requirements for a given document asynchronously.
    """
    logger.info(f"Starting background adjudication for doc_id={doc_id}")
    
    # Persistent Task Tracking: Mark as processing
    await db_queue.put(("upsert_task", (doc_id, "processing"), {}))
    await db_queue.put(("update_document_status", (doc_id, "processing"), {}))
    
    # Fetch requirements (Read is fine without queue)
    reqs = db.get_requirements_for_doc(doc_id)
    if not reqs:
        logger.warning(f"No requirements found for doc_id={doc_id}")
        await db_queue.put(("update_document_status", (doc_id, "failed"), {}))
        await db_queue.put(("upsert_task", (doc_id, "failed"), {}))
        return

    # Pass the write queue to the adjudicator
    adjudicator = AdjudicationManager(db_write_queue=db_queue)
    
    # We will use an asyncio.Semaphore to limit concurrent outbound requests
    semaphore = asyncio.Semaphore(config.BATCH_SIZE)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        async def bounded_adjudicate(req):
            async with semaphore:
                try:
                    await adjudicator.adjudicate_requirement_async(req["id"], client, portfolio=portfolio)
                except Exception as e:
                    logger.error(f"Error adjudicating req_id={req['id']}: {e}")

        # Run all requirements concurrently
        tasks = [bounded_adjudicate(req) for req in reqs]
        await asyncio.gather(*tasks)

    # All done
    logger.info(f"Finished background adjudication for doc_id={doc_id}")
    await db_queue.put(("update_document_status", (doc_id, "completed"), {}))
    await db_queue.put(("delete_task", (doc_id,), {})) # Remove from active queue

@app.post("/analyze/{doc_id}")
async def analyze_document(doc_id: int, background_tasks: BackgroundTasks, portfolio: str = None):
    """
    Kicks off the asynchronous adjudication pipeline.
    """
    # Verify document exists and hasn't already been processed
    try:
        progress = db.get_adjudication_progress(doc_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if progress.get("status") == "completed":
        return {"message": "Document already analyzed", "doc_id": doc_id, "status": "completed"}
        
    background_tasks.add_task(process_document_background, doc_id, portfolio)
    return {"message": "Analysis started in background", "doc_id": doc_id, "status": "processing"}

@app.get("/status/{doc_id}")
async def get_status(doc_id: int):
    """
    Returns the current progress of the adjudication pipeline.
    """
    try:
        progress = db.get_adjudication_progress(doc_id)
        if progress["total"] == 0:
            return {"status": progress["status"], "progress": 0.0, "completed": 0, "total": 0}
            
        pct = (progress["completed"] / progress["total"]) * 100
        return {
            "status": progress["status"],
            "progress": round(pct, 2),
            "completed": progress["completed"],
            "total": progress["total"]
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Document not found")
