import os
import json
import time
import base64
import logging
import redis
import psycopg2
import requests
import cv2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DocWorker")

class DocumentProcessor:
    def __init__(self):
        self.queue = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, db=0, decode_responses=True)
        self.db_host = os.getenv("DB_HOST", "postgres")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
        
    def _get_db(self):
        return psycopg2.connect(host=self.db_host, database="doc_intel", user="admin", password="password")

    def preprocess_image(self, filepath):
        """Use OpenCV to clean the document for the AI"""
        try:
            logger.info("Applying OpenCV preprocessing...")
            img = cv2.imread(filepath)
            # Convert to grayscale to remove complex color noise for the AI
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # Save the cleaned image over the original
            cv2.imwrite(filepath, gray)
            return filepath
        except Exception as e:
            logger.error(f"OpenCV failed: {e}")
            return filepath

    def extract_with_llava(self, filepath):
        """Send the cleaned image to local Ollama LLaVA model"""
        logger.info("Sending to Ollama (LLaVA) for intelligence extraction...")
        
        with open(filepath, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')

        payload = {
            "model": "llava",
            "prompt": "Analyze this document. Extract the 'vendor_name', 'total_amount', and 'date'. Return ONLY a strict JSON object.",
            "images": [encoded_image],
            "stream": False,
            "format": "json"
        }

        try:
            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=60)
            response.raise_for_status()
            # Parse the JSON string returned by the AI into a Python dictionary
            return json.loads(response.json().get('response', '{}'))
        except Exception as e:
            logger.error(f"AI Extraction failed: {e}")
            return {"error": "Failed to parse document"}

    def run(self):
        logger.info("Worker listening to 'document_queue'...")
        while True:
            try:
                # 1. Pull from Redis Queue
                _, filepath = self.queue.brpop("document_queue")
                logger.info(f"Processing: {filepath}")
                
                # 2. Preprocess with OpenCV (if it's an image)
                if filepath.lower().endswith(('.png', '.jpg', '.jpeg')):
                    self.preprocess_image(filepath)
                
                # 3. AI Extraction
                structured_data = self.extract_with_llava(filepath)
                
                # 4. Save to Database
                with self._get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO processed_documents (filename, structured_data) VALUES (%s, %s)",
                            (os.path.basename(filepath), json.dumps(structured_data))
                        )
                    conn.commit()
                logger.info(f"Successfully processed and saved to DB! Extracted: {structured_data}")
                
            except Exception as e:
                logger.error(f"System Error: {e}")
                time.sleep(2)

if __name__ == "__main__":
    time.sleep(5) # Wait for DB to boot
    DocumentProcessor().run()